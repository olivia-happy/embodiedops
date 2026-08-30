# EmbodiedOps real-robot data card

## Purpose and boundary

EmbodiedOps ships with synthetic tabletop manipulation fixtures only. They are
regression data, not performance evidence. No Unitree, UBTECH, Fourier, or
other company internal data is included in this repository. The project does
not connect to, command, or import from a physical robot.

Any real-robot export must be an explicit, approved input that validates against
`RealRobotDataCard` and is paired with version-scoped `RootCauseAnnotation`
records. Automatic import and cloud upload are intentionally out of scope.

## Required provenance before a real-data claim

Every real export must declare:

- immutable `dataset_version_id` and a human-readable `source_name`;
- `real_robot_data=true`, plus robot model, firmware, and task family;
- a non-empty license, consent, or internal permission reference;
- redaction status of `redacted` or `reviewed` (never `not_applicable`);
- a holdout policy that identifies how sites, scenes, operators, or time
  windows are separated for evaluation.

Permission must be specific enough for a reviewer to locate the approval. A
placeholder such as `unknown`, `none`, or `not_applicable` is rejected for a
real-robot claim.

## Annotation protocol

Each root-cause annotation is tied to exactly one dataset version and one
episode. Its supporting and counter evidence can reference only event IDs
inside that episode. Confidence is a finite value in `[0, 1]`.

Review states are `unreviewed`, `single_review`, `double_review`, and
`adjudicated`. An adjudicated decision requires a distinct reviewer identity.
Annotation fields contain no raw camera frame, prompt, model response, or
unredacted sensor payload.

## Privacy, retention, and deletion

Before export, remove or redact people, voice, facility identifiers, account
IDs, and any sensitive telemetry not needed for failure analysis. Store a
deletion contact and the retention rule with the approval record outside this
public repository. If permission is withdrawn, revoke the dataset version from
evaluation and delete its local export and derived public artifacts according to
the approval record.

## Evaluation use

Use holdout data only after provenance and annotations have been reviewed.
Report synthetic, approved real, and live-model results separately. A successful
synthetic regression or annotation-contract test is not evidence of sim-to-real
transfer, production safety, or ROI.
