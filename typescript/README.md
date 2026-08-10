# vicary (TypeScript)

The npm front door. **Not published yet** — under construction.

The detector, the data asset and the measured numbers are described in the
[project README](https://github.com/bwthomas/vicary#readme). What lives here is a
port, and the bar it has to clear before it is published is the shared
conformance suite in [`conformance/`](../conformance): for every fixture frame it
must produce **byte-identical output to the Python implementation, placeholder
numbering included**.

Until `npm run conformance` is green against the full frame set, nothing here is
a redactor you should point at student writing.
