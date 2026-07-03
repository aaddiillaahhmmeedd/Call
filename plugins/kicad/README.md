# KiCad action plugin: Netlist Agent board report

Adds a **Netlist Agent: Board Report** button (category *Analysis*) to the
pcbnew toolbar and `Tools → External Plugins` menu. Clicking it:

1. Saves the currently open board to a temporary copy (unsaved edits are
   included; the file on disk is not touched). If the board has never been
   saved to a file, a message box asks you to save it first.
2. Runs `python -m netlist_agent.cli report <board> -o <tempdir>/report.html`
   with KiCad's python interpreter — the same ratsnest + DRC + board-SVG
   report as the CLI's `report` subcommand.
3. Opens the resulting standalone HTML report in your default browser
   (click a net row to highlight it, wheel-zoom / drag-pan the board).

## Requirements

The `netlist-agent` package must be importable by **KiCad's** python (not
just your system python). From this repository:

```bash
# Linux/macOS: KiCad usually uses the system python3
python3 -m pip install -e /path/to/this/repo

# Windows: use the interpreter bundled with KiCad
"C:\Program Files\KiCad\<version>\bin\python.exe" -m pip install -e C:\path\to\this\repo
```

## Install the plugin

Copy (or symlink) the `netlist_agent_plugin` folder — the folder itself,
not just its files — into KiCad's plugin search path:

| Platform | Directory |
| --- | --- |
| Linux | `~/.local/share/kicad/<version>/scripting/plugins/` |
| macOS | `~/Documents/KiCad/<version>/scripting/plugins/` |
| Windows | `%USERPROFILE%\Documents\KiCad\<version>\scripting\plugins\` |

For example on Linux:

```bash
mkdir -p ~/.local/share/kicad/8.0/scripting/plugins
ln -s /path/to/this/repo/plugins/kicad/netlist_agent_plugin \
      ~/.local/share/kicad/8.0/scripting/plugins/netlist_agent_plugin
```

Then restart pcbnew, or run `Tools → External Plugins → Refresh Plugins`.
(The exact path is listed in KiCad under
`Preferences → Configure Paths` / the PCB editor's scripting console via
`import pcbnew; pcbnew.PLUGIN_DIRECTORIES_SEARCH`.)

The package imports cleanly outside KiCad (the `pcbnew` import is guarded),
so having it on `PYTHONPATH` elsewhere is harmless.
