# Appwrite backend cutover

The application board already reads and writes Appwrite directly. This cutover
removes Render from the remaining interactive request path.

## Target architecture

- TablesDB stores resumes, immutable versions, revision messages, verified
  profile facts, fact bullets, and asynchronous agent jobs.
- Storage stores imported source files and finalized PDFs.
- Browser CRUD uses the authenticated Appwrite Web SDK directly.
- The `job-os-agents` Python Function handles only expensive extraction,
  revision, review, finalization, tailoring, parsing, and discovery work.
- Agent calls are asynchronous. The UI creates a queued job immediately and
  reads its status from TablesDB instead of holding an HTTP request open.
- Neon remains read-only during verification and is not deleted during the
  cutover.

Appwrite Functions have a configurable timeout up to 900 seconds, but they may
still cold-start. Keeping CRUD out of Functions makes those cold starts
irrelevant to page loads, refreshes, and ordinary edits.

## Safe migration order

1. Run `pnpm appwrite:bootstrap` with the existing server key.
2. Create and deploy the `job-os-agents` Python 3.12 Function from the existing
   GitHub repository.
3. Grant the Function row read/write and file read/write scopes.
4. Add the existing Manifest/Anthropic variables as secret Function variables.
5. Run `pnpm appwrite:migrate-workspace` from an environment that has both the
   Neon database URL and Appwrite server key.
6. Require exact count verification for resumes, versions, revision messages,
   profile facts, and fact bullets.
7. Switch Resume Studio and Profile to the Appwrite workspace client.
8. Verify import, edit, archive, proposal, review, finalize, PDF download, and
   GitHub README evidence in production.
9. Remove `API_BASE_URL` and the Render warmup component only after every
   remaining proxy route has an Appwrite replacement.

Rollback is a frontend environment switch while Neon remains intact.
