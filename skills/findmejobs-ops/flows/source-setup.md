## Source Setup

1. `.venv/bin/findmejobs sources list --json`
2. `.venv/bin/findmejobs sources add --json '<source-object>'`
3. Optional tuning:
- `.venv/bin/findmejobs sources set <name> --priority <n> --trust-weight <w>`
- `.venv/bin/findmejobs sources disable <name>`
- `.venv/bin/findmejobs sources remove <name> --yes`
4. `.venv/bin/findmejobs config validate --json`
5. `.venv/bin/findmejobs doctor --json`
