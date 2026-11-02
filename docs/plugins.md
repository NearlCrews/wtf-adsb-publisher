# Publisher plugins

Install a plugin package and select it in the configuration. Discovery reads entry-point metadata; only the configured plugin is imported. Duplicate names, unsupported API versions, unknown binding fields, and missing operations stop startup before publication. Each factory validates its own settings; the built-in factories accept no settings.

| Role | Entry-point group | Required operation | Built-in name |
| --- | --- | --- | --- |
| Evidence reader | `wtf_adsb_publisher.reader` | `read(config) -> inbox_state` | `json-inbox` |
| Editorial policy | `wtf_adsb_publisher.policy` | `select(state, now, *, since, ledger) -> candidates` | `site-policy` |
| Paid writing transport | `wtf_adsb_publisher.writer` | `send(encoded, key) -> response` | `openai` |
| Media renderer | `wtf_adsb_publisher.renderer` | `render(default_renderer, *args, **kwargs)` | `site-media` |
| Publishing target | `wtf_adsb_publisher.publisher` | `connect(config) -> client` | `ghost` |
| Deployment target | `wtf_adsb_publisher.deployer` | `deploy(default_deployer, *args, **kwargs)` | `static-ssh` |

An entry point names a factory that receives its `settings` object. The returned object has a `Descriptor(api=">=1,<2", role="...")` from `wtf_adsb_publisher.plugins`, plus the role's operation. Built-ins follow the same discovery path as external packages.

```toml
[project.entry-points."wtf_adsb_publisher.reader"]
company-feed = "company_feed:Reader"
```

```json
{
  "plugins": {
    "reader": {
      "plugin": "company-feed",
      "settings": {"inbox": "/srv/company-feed/inbox.sqlite3"}
    }
  }
}
```

Readers supply the validated collection profile returned by `ingest.read_state`. A policy supplies candidates; the core still rechecks their evidence and owns publication history, quotas, privacy, and retries. Writing transports execute a request already reserved by the core's budget store. The core saves responses before settlement and validates resulting prose. Plugins cannot disable those checks through their configuration.

The selected writing transport handles story prose, aircraft-type research, and
weekly research. It receives the prepared request, including any search tools,
and must return the response shape expected by the corresponding validator. The
built-in OpenAI transport preserves the separate story and research timeouts.

A publication pass reuses the plugin instances validated during startup. Release
fingerprints include renderer and deployer settings, entry points, distribution
names, and installed versions. Updating either integration triggers a new release
even when the evidence has not changed.

The first publishing interface preserves the existing client's inventory, ownership, revision, and uncertain-write reconciliation behavior. A replacement CMS adapter must implement that interface; it must not claim success before the target confirms or reconciles the write. Renderer and deployment adapters receive the existing implementation as an optional delegate, so they can replace or extend it without editing core files.

These are trusted Python extensions with the publisher process's permissions. They are not a sandbox. Plugin code, schema versions, and configuration belong in the deployment's dependency lock. The publisher and engine have separate entry-point groups and release cycles.

The JSON consumer accepts enrichment schemas only after the deployment explicitly reviews and pins their local files. Add entries to the top-level `extension_schemas` array in publisher configuration, or supply that array as a JSON file with `wtf-adsb-publisher ingest --extension-catalog /path/to/catalog.json`. Each entry contains `namespace`, `schema_version`, an absolute `path`, and the canonical JSON `sha256` digest. The consumer checks the pin and validates each extension payload without fetching schema URLs.

Unknown, changed, or invalid extension payloads remain in the durable inbox under quarantine. This first release holds new publication for the whole feed while any active observation is quarantined. Explicit retractions still apply. It does not selectively approve stories that appear unrelated to an unknown extension: that needs a richer, explicit dependency contract. Reimporting with a reviewed schema catalog reevaluates quarantine without changing event identities or cursors; removing an approval restores the hold.
