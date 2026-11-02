# Publisher documentation

- [Quick start](../README.md#quick-start-with-docker) and [hosting the site](hosting.md)
- [Setup interview](setup-interview.md) for an AI assistant or a person, with [AGENTS.md](../AGENTS.md)
- [Development setup](../DEVELOPMENT.md#development-setup) and [contributing](../CONTRIBUTING.md)
- [Configuration, publication, and recovery](operations.md)
- [Docker image and storage](docker.md) and [scheduled container jobs](container-jobs.md)
- [Migration and rollback](migration.md)
- [Production cutover gates and handoff](cutover.md)
- [Plugin interfaces](plugins.md)
- [Evidence contracts](../contracts/README.md) and [site snapshots](site-snapshot.md)
- [Theme builds, media, and browser checks](../tools/ghost-theme/README.md)
- [Runtime reference data](../src/wtf_adsb_publisher/data/README.md) and [map data](../tools/ghost-theme/data/README.md)
- [Dependency audit](dependency-audit-2026-10-08.md)
- [Security](../SECURITY.md), [support](../SUPPORT.md), and [third-party notices](../THIRD_PARTY_NOTICES.md)

The publisher reads the engine's versioned evidence feed and never opens the
engine's database. The source checkout holds the site tooling that publication
needs, and the Docker image bundles all of it; the installed Python wheel alone
does not.
