# Safe publishing checklist

- [ ] All code was newly written for this demonstration (clean room, not an employer-derived copy).
- [ ] No exports, OEM lists, customer identifiers, URLs requiring authentication or real commercial prices.
- [ ] No employer names, logos, credentials, connection strings or internal taxonomy.
- [ ] No code, schema or trade secrets transferred without rights/authorization.
- [ ] Never publish `.env`, data/demo.sqlite or generated `runs/` accidentally.
- [ ] Verify `git status`, `git diff --cached`, GitHub history and GitHub secret scanning before publication.
- [ ] Report limitations: fabricated market data, no real scraping or commercial coverage.
- [ ] Show tests, model/agent boundary, failed cases and observable evidence.
- [ ] For public showcase, clearly label synthetic outputs and do not claim production deployment.
