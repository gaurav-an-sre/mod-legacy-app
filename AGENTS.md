# Extraction agent rules

- Never modify `legacy/` or `db/`; they are the immutable source system and
  deterministic fixture for parity.
- Never change a route weight in `strangler/routes.yaml`; the cutover
  controller owns promotion and rollback.
