"""Create or inspect the Bedrock Guardrail that ``guardrail`` mode needs.

:mod:`vicary.redaction` deliberately creates no AWS resource: enabling
``guardrail`` mode without a configured Guardrail ID raises rather than shipping
student text unredacted. That posture is right and it leaves a gap — a deployment
that turns the mode on before the resource exists raises on every request — so
this module is the missing half. It provisions the resource reproducibly and
prints the two configuration values a deployment then needs.

Run it as ``python -m vicary.bedrock.guardrail``. Requires ``vicary[bedrock]``
and AWS credentials with Bedrock Guardrail permissions.

Why the entity list is what it is
---------------------------------
Student compositions are prose about the writer's own life, so the entities that
actually appear are ``NAME``, ``ADDRESS``, ``AGE``, ``PHONE``, ``EMAIL`` — not
payment or credential data. The financial/credential entities are included
anyway because they are free (the policy is billed per text unit evaluated, not
per entity type) and a false negative on one is far more costly than the nothing
it costs to list it.

Every entity is ``ANONYMIZE``, never ``BLOCK``. A blocked composition is a student
who gets nothing back; a masked one still gets processed. A consuming pipeline's
job is to handle whatever it is handed, so the failure mode chosen here is
"processed with a placeholder in place of a name".

``version`` is ``DRAFT`` unless you publish one. ``DRAFT`` is mutable, which is
wrong for production: publish a numbered version and pin
``VICARY_BEDROCK_GUARDRAIL_VERSION`` to it, so a console edit cannot silently
change what a running deployment redacts.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Any

from vicary import config

logger = logging.getLogger(__name__)

#: Default resource name. Stable, so a re-run finds the existing one rather than
#: creating a second Guardrail that differs from the one production points at.
DEFAULT_NAME: str = "vicary-name-redaction"

#: PII entities masked on both the inbound and outbound pass. ``ANONYMIZE``
#: replaces the span with a typed placeholder (``{NAME}``) rather than dropping
#: the essay.
PII_ENTITIES: tuple[str, ...] = (
    # What student prose actually contains.
    "NAME",
    "ADDRESS",
    "AGE",
    "PHONE",
    "EMAIL",
    "USERNAME",
    "URL",
    # Free to add, expensive to miss.
    "US_SOCIAL_SECURITY_NUMBER",
    "CREDIT_DEBIT_CARD_NUMBER",
    "CREDIT_DEBIT_CARD_CVV",
    "CREDIT_DEBIT_CARD_EXPIRY",
    "PIN",
    "PASSWORD",
    "DRIVER_ID",
    "LICENSE_PLATE",
    "US_PASSPORT_NUMBER",
    "US_BANK_ACCOUNT_NUMBER",
    "US_BANK_ROUTING_NUMBER",
    "IP_ADDRESS",
    "MAC_ADDRESS",
    "AWS_ACCESS_KEY",
    "AWS_SECRET_KEY",
)

#: Never reached — every entity is ANONYMIZE, so the Guardrail cannot block. The
#: API requires the fields regardless.
_BLOCKED_MESSAGING: str = (
    "This text could not be processed because it contains information that "
    "cannot be handled."
)


def _client(region: str) -> Any:
    import boto3

    return boto3.client("bedrock", region_name=region)


def find_guardrail(client: Any, name: str) -> dict[str, Any] | None:
    """The Guardrail named ``name``, or ``None``.

    Paginates, because ``list_guardrails`` truncates and a partial scan that
    silently missed an existing resource would create a duplicate.
    """
    token: str | None = None
    while True:
        kwargs: dict[str, Any] = {"maxResults": 100}
        if token:
            kwargs["nextToken"] = token
        page = client.list_guardrails(**kwargs)
        for entry in page.get("guardrails", []):
            if entry.get("name") == name:
                return entry
        token = page.get("nextToken")
        if not token:
            return None


def sensitive_information_policy() -> dict[str, Any]:
    """The PII policy config: every entity in :data:`PII_ENTITIES`, ANONYMIZE."""
    return {
        "piiEntitiesConfig": [
            {"type": entity, "action": "ANONYMIZE"} for entity in PII_ENTITIES
        ]
    }


def create_guardrail(client: Any, name: str, *, description: str) -> dict[str, Any]:
    """Create the Guardrail and return the API response."""
    return client.create_guardrail(
        name=name,
        description=description,
        blockedInputMessaging=_BLOCKED_MESSAGING,
        blockedOutputsMessaging=_BLOCKED_MESSAGING,
        sensitiveInformationPolicyConfig=sensitive_information_policy(),
    )


def update_guardrail(client: Any, guardrail_id: str, name: str, *,
                     description: str) -> dict[str, Any]:
    """Push :data:`PII_ENTITIES` onto an existing Guardrail (edits ``DRAFT``)."""
    return client.update_guardrail(
        guardrailIdentifier=guardrail_id,
        name=name,
        description=description,
        blockedInputMessaging=_BLOCKED_MESSAGING,
        blockedOutputsMessaging=_BLOCKED_MESSAGING,
        sensitiveInformationPolicyConfig=sensitive_information_policy(),
    )


def entity_drift(client: Any, guardrail_id: str, version: str) -> dict[str, list[str]]:
    """``{"missing": [...], "not_anonymize": [...]}`` for a live Guardrail.

    The check that matters operationally: a console edit that drops ``NAME`` or
    flips it to ``BLOCK`` is invisible from inside the pipeline, which only ever
    sees "no intervention" and reads it as clean text.
    """
    live = client.get_guardrail(guardrailIdentifier=guardrail_id,
                               guardrailVersion=version)
    policy = live.get("sensitiveInformationPolicy") or {}
    by_type = {e.get("type"): e.get("action")
               for e in policy.get("piiEntities") or []}
    return {
        "missing": [e for e in PII_ENTITIES if e not in by_type],
        "not_anonymize": [e for e in PII_ENTITIES
                          if e in by_type and by_type[e] != "ANONYMIZE"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create or verify the Guardrail used by vicary's "
                    "guardrail mode.")
    # No default region. A Guardrail is regional and a wrong guess presents as
    # "the guardrail identifier or version provided does not exist", which reads
    # like a missing resource rather than a cross-region lookup.
    parser.add_argument("--region", required=True,
                        help="AWS region to create or inspect the Guardrail in. "
                             "Must match "
                             f"{config.GUARDRAIL_REGION_ENV_VAR} where the "
                             "redactor runs.")
    parser.add_argument("--name", default=DEFAULT_NAME)
    parser.add_argument("--description",
                        default="Entity anonymization for student compositions "
                                "(inbound text and outbound generated text).")
    parser.add_argument("--apply", action="store_true",
                        help="create it, or update an existing one to match "
                             "PII_ENTITIES. Without this the run only reports.")
    parser.add_argument("--publish", action="store_true",
                        help="publish a numbered version after applying. "
                             "Production should pin a number, not DRAFT.")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    client = _client(args.region)

    existing = find_guardrail(client, args.name)
    if existing is None:
        print(f"guardrail {args.name!r}: ABSENT in {args.region}")
        if not args.apply:
            print("\nGuardrail mode raises without a resource, so a deployment "
                  "configured for it would fail on every request.\n"
                  "Re-run with --apply to create it.")
            return 1
        created = create_guardrail(client, args.name,
                                   description=args.description)
        guardrail_id = created["guardrailId"]
        version = created.get("version", "DRAFT")
        print(f"CREATED {args.name} id={guardrail_id} version={version}")
    else:
        guardrail_id = existing["id"]
        version = existing.get("version", "DRAFT")
        print(f"guardrail {args.name!r}: PRESENT id={guardrail_id} "
              f"version={version}")
        if args.apply:
            update_guardrail(client, guardrail_id, args.name,
                             description=args.description)
            print(f"UPDATED {guardrail_id} to {len(PII_ENTITIES)} ANONYMIZE "
                  f"entities")

    drift = entity_drift(client, guardrail_id, version)
    if drift["missing"] or drift["not_anonymize"]:
        print(f"DRIFT {json.dumps(drift)}")
    else:
        print(f"entities OK: {len(PII_ENTITIES)} present, all ANONYMIZE")

    if args.publish:
        published = client.create_guardrail_version(
            guardrailIdentifier=guardrail_id,
            description="Pinned for production.")
        version = published["version"]
        print(f"PUBLISHED version={version}")

    print("\nConfigure the redactor with:")
    print(f"  {config.REDACTION_ENV_VAR}=guardrail")
    print(f"  {config.GUARDRAIL_ID_ENV_VAR}={guardrail_id}")
    print(f"  {config.GUARDRAIL_VERSION_ENV_VAR}={version}")
    print(f"  {config.GUARDRAIL_REGION_ENV_VAR}={args.region}")
    if version == "DRAFT":
        print("  ^ DRAFT is MUTABLE. Publish a version before production "
              "depends on it (--publish).")
    return 0 if not (drift["missing"] or drift["not_anonymize"]) else 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
