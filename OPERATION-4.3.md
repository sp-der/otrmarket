# Operation 4.3 - Permanent Hosting

Makes the Operation 4.2 build deployable as an always-on Railway service while
remaining compatible with local Codespaces development.

- Docker runtime no longer depends on a checked-in `.venv`.
- Dashboard honors Railway's injected `PORT` value.
- Railway config uses the existing health endpoint and an always-restart policy.
- Deployment guide specifies a persistent `/app/data` volume for SQLite.
- Recommended permanent endpoint: `market.otrservices.com`.
