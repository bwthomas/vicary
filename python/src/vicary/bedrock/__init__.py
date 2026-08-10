"""Optional AWS Bedrock Guardrail arm. Install ``vicary[bedrock]`` to use it.

Two reasons this exists rather than being deleted. It is a managed detector some
hosts are already paying for and would rather use; and it is the **external
baseline** the offline detector is scored against, without which the offline
numbers would be self-reported with nothing to reconcile them to.

It is billed per call, it needs a provisioned Guardrail resource, and it is never
the default.
"""
