"""CLI: python -m connectome {download,build} [...]"""

from __future__ import annotations

import argparse


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m connectome", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("download", help="fetch DWI, templates and atlases")
    b = sub.add_parser("build", help="download (if needed) and build the connectome")
    for q in (d, b):
        q.add_argument("--data-dir", default="data/connectome")
        q.add_argument("--subject", default="01", help="ds000114 subject (01-10)")
    b.add_argument("--out-dir", default="results/connectome")
    b.add_argument("--skip-brain", action="store_true", help="skip brain tractography (fast)")
    b.add_argument("--skip-spinal-dwi", action="store_true")
    b.add_argument("--save-tractograms", action="store_true", help="also write .trk files")
    b.add_argument("--no-3d", action="store_true", help="skip the connectome3d.html viewer")
    a = p.parse_args(argv)
    if a.cmd == "download":
        from .download import download_all
        download_all(a.data_dir, subject=a.subject)
    else:
        from .pipeline import run
        run(a.data_dir, a.out_dir, subject=a.subject, skip_brain=a.skip_brain,
            skip_spinal_dwi=a.skip_spinal_dwi, save_tractograms=a.save_tractograms,
            viewer_3d=not a.no_3d)


if __name__ == "__main__":
    main()
