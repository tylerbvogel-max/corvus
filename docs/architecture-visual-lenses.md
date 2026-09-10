# Architecture visual lenses

Observe -> Architecture -> System offers four coordinated views. These consume
the existing reviewed architecture payload, not a second architecture model.

* Boundaries places backend and process-local cache zones inside a shared
  modular-monolith perimeter. Other zones remain outside that process. Slabs
  are responsibilities, not individual deployed services.
* Process paths draw the ordered steps of one reviewed process. Arrows mean
  sequence, not live traffic, an import dependency, or a synchronous invocation.
  The inspector carries the existing owner, state change, guardrail and source
  evidence. Failure/recovery descriptions are deployment-wide and labeled so.
* Code footprint scales each component's top-face area by source lines relative
  to the selected group. A 4% minimum visible area keeps tiny entries selectable;
  exact line/file counts are authoritative. Height has no metric meaning. This
  does not measure importance, complexity, memory use or inference cost.
* Operational receipts loads the existing job-health adapter only when opened.
  It displays recorded outcomes, timestamps and nonnegative numeric durations.
  Zero duration remains zero; malformed/missing durations remain unavailable.
  Failed loading never produces a healthy summary. No arbitrary receipt detail
  or exception body is rendered. Model expenditure and CPU/memory/disk allocation
  are explicitly unavailable because this endpoint does not support them.

The identity strip distinguishes source-model freshness, review date and source
fingerprint. The current architecture API does not supply the deployed source
SHA; the UI says unavailable rather than substituting an extraction fingerprint.
Source freshness is not proof that the latest public main is deployed. Reviewed
prose and evidence linkage do not mechanically prove every claimed behavior.

`frontend/scripts/verify-architecture-lenses.mjs` exercises the normal packaged
build at desktop and phone widths against source-derived architecture and
synthetic API fixtures. It covers each lens, state/guardrail inspection, keyboard
selection, footprint inventory, zero/missing/malformed receipts, failed loading,
private-detail exclusion and stale source labeling. Required frontend CI runs it.
The fixture returns an array for `/models`; the older isometric-only verifier's
catch-all object response did not satisfy that contract on the phone home screen.

These views do not authorize public exposure of the personal backend, new
telemetry collectors, provider expenditure, or a security-certification claim.

## Integrated memory map correction

The primary map now has its own sizing and flow controls. Its backend perimeter
is computed from all backend/cache slab face vertices in the same isometric
coordinate plane, including extrusion and padding; it is not a separate guessed
diamond. Tests check model geometry and rendered SVG face containment.

Primary-map area represents **exclusive evidence-linked source file count**,
not complete zone ownership, LOC, RAM or storage. References are deduplicated by
file; files referenced by multiple zones are listed in one shared pool and never
silently apportioned or counted twice. A 20.25% minimum visible area preserves
selectability. Exact counts and file lists are available; equal-area mode remains.
The separate component LOC lens retains its different, explicitly labeled basis.

Inter-zone arrows are reviewed data/control flows with source pointers. Capture,
recall and maintenance filters operate on the same map. Selecting a zone limits
connections to its neighbors; selecting a connection explains its direction,
transport and boundary semantics. Database-read arrows denote data direction,
not the side initiating SQL. These are not observed traffic or a mechanically
extracted call graph. Missing endpoints are not invented.
