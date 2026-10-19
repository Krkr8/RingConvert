# SPDX-License-Identifier: GPL-3.0-or-later
"""ringconvert-cli: every conversion and tool from the terminal.

    ringconvert-cli FILE... --to jpg
    ringconvert-cli FILE --list
    ringconvert-cli FILE --tool compress
    ringconvert-cli FILE --tool metadata [--strip]
    ringconvert-cli FILE --tool crop (--box X,Y,W,H | --ratio 16:9)
    ringconvert-cli FILE --tool edit [--rotate 90] [--flip-h] [--resize 800x] [--brightness 1.1] ...
    ringconvert-cli FILE --tool add-bg [--background gradient:#f60,#90f] [--padding 120] ...
    ringconvert-cli FILE --tool annotate --shapes shapes.json
    ringconvert-cli FILE --tool redact --rect [PAGE:]X,Y,W,H [--rect ...]
    ringconvert-cli --check
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .core import backends, compress, imageops, metadata, pdf
from .core.convert import convert
from .core.detect import ALL_TOOLS, detect, label, targets, tools
from .core.errors import RingConvertError
from .core.naming import Result

TOOL_ALIASES = {"add-bg": "add_bg", "addbg": "add_bg", "bg": "add_bg", "meta": "metadata"}


def _floats(text: str, count: int, what: str) -> list[float]:
    parts = text.split(",")
    if len(parts) != count:
        raise argparse.ArgumentTypeError(f"{what} needs {count} comma-separated numbers")
    try:
        return [float(p) for p in parts]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{what}: {exc}") from exc


def box_arg(text: str) -> tuple[int, int, int, int]:
    return tuple(round(v) for v in _floats(text, 4, "--box"))


def rect_arg(text: str) -> tuple[int, float, float, float, float]:
    """`[PAGE:]X,Y,W,H` with a 1-based page number (default 1)."""
    page, sep, rest = text.partition(":")
    if not sep:
        page, rest = "1", text
    try:
        page_no = int(page) - 1
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"--rect page must be a number: {page!r}") from exc
    return (page_no, *_floats(rest, 4, "--rect"))


def size_arg(text: str) -> tuple[int | None, int | None]:
    """"800x600", "800x" or "x600"."""
    w, sep, h = text.lower().partition("x")
    if not sep:
        raise argparse.ArgumentTypeError("--resize looks like 800x600, 800x or x600")
    try:
        return (int(w) if w else None, int(h) if h else None)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"--resize: {exc}") from exc


def background_arg(text: str) -> imageops.Background:
    """gradient:#a,#b[,...][@angle] | solid:#c | image:/path | a preset number."""
    if text.isdigit():
        presets = [("gradient", g) for g in imageops.GRADIENTS] + [("solid", [c]) for c in imageops.SOLIDS]
        index = int(text) - 1
        if not 0 <= index < len(presets):
            raise argparse.ArgumentTypeError(f"--background preset must be 1..{len(presets)}")
        kind, colors = presets[index]
        return imageops.Background(kind=kind, colors=list(colors))
    kind, sep, value = text.partition(":")
    if not sep or kind not in ("gradient", "solid", "image"):
        raise argparse.ArgumentTypeError("--background is gradient:#a,#b[@angle], solid:#c or image:PATH")
    if kind == "image":
        return imageops.Background(kind="image", path=Path(value).expanduser())
    angle = 135.0
    if "@" in value:
        value, _, angle_text = value.partition("@")
        angle = float(angle_text)
    colors = [c.strip() for c in value.split(",") if c.strip()]
    if not colors:
        raise argparse.ArgumentTypeError("--background needs at least one colour")
    for c in colors:
        imageops.parse_color(c)
    return imageops.Background(kind=kind, colors=colors, angle=angle)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ringconvert-cli",
        description="Convert files and run RingConvert tools. Results are saved next to the original.",
    )
    p.add_argument("files", nargs="*", type=Path, metavar="FILE")
    action = p.add_mutually_exclusive_group()
    action.add_argument("--to", metavar="FORMAT", help="target format, e.g. jpg, pdf, docx, mp3")
    action.add_argument("--tool", metavar="TOOL", help="one of: " + ", ".join(t.replace("_", "-") for t in ALL_TOOLS))
    action.add_argument("--list", action="store_true", help="show the ring menus for each file")
    action.add_argument("--check", action="store_true", help="show which backends are installed")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("-q", "--quiet", action="store_true", help="only print output paths")

    g = p.add_argument_group("metadata")
    g.add_argument("--strip", action="store_true", help="write a ' Clean' copy without metadata")
    g.add_argument("--all", action="store_true", help="also list file-system tags")

    g = p.add_argument_group("crop")
    g.add_argument("--box", type=box_arg, metavar="X,Y,W,H", help="crop area in pixels")
    g.add_argument("--ratio", metavar="W:H", help="crop/add-bg ratio: free|auto|1:1|16:9|9:16|4:3|3:4|4:5")

    g = p.add_argument_group("edit")
    g.add_argument("--rotate", type=int, default=0, choices=(0, 90, 180, 270, -90))
    g.add_argument("--flip-h", action="store_true")
    g.add_argument("--flip-v", action="store_true")
    g.add_argument("--resize", type=size_arg, metavar="WxH")
    g.add_argument("--brightness", type=float, default=1.0)
    g.add_argument("--contrast", type=float, default=1.0)
    g.add_argument("--saturation", type=float, default=1.0)

    g = p.add_argument_group("add-bg")
    g.add_argument("--background", type=background_arg, metavar="SPEC",
                   help="gradient:#a,#b[@angle] | solid:#c | image:PATH | preset number")
    g.add_argument("--padding", type=int, default=120)
    g.add_argument("--corners", type=int, default=40)
    g.add_argument("--shadow", type=int, default=24)

    g = p.add_argument_group("annotate / redact")
    g.add_argument("--shapes", type=Path, metavar="JSON", help="annotation shapes (see imageops.py)")
    g.add_argument("--rect", type=rect_arg, action="append", metavar="[PAGE:]X,Y,W,H",
                   help="redaction box; px for images, PDF points for PDFs (repeatable)")
    return p


class Reporter:
    def __init__(self, quiet: bool):
        self.quiet = quiet
        self.last = -1

    def progress(self, fraction: float) -> None:
        if self.quiet or not sys.stderr.isatty():
            return
        pct = int(fraction * 100)
        if pct != self.last:
            self.last = pct
            print(f"\r  {pct:3d}%", end="", file=sys.stderr, flush=True)

    def done(self, result: Result) -> None:
        if self.last >= 0:
            print("\r" + " " * 8 + "\r", end="", file=sys.stderr)
            self.last = -1
        for out in result.outputs:
            print(out if self.quiet else f"→ {out}", flush=True)
        if not self.quiet:
            for note in result.notes:
                print(f"  {note}", file=sys.stderr)


def show_list(path: Path) -> None:
    info = detect(path)
    print(f"{path}  [{info.mime}, {info.kind or 'unknown'}]")
    print("  convert:", " ".join(label(f) for f in targets(info)) or "-")
    print("  tools:  ", " ".join(label(t) for t in tools(info)) or "-")


def show_check() -> int:
    ok = True
    for name, present in backends.status().items():
        hint = "" if present else f"   ({backends.HINTS.get(name, '')})" if backends.HINTS.get(name) else ""
        print(f"  {'✓' if present else '✗'} {name}{hint}")
        ok &= present
    return 0 if ok else 1


def run_tool(path: Path, tool: str, args, reporter: Reporter) -> Result:
    info = detect(path)
    if info.kind is None:
        raise RingConvertError(f"{path.name}: unrecognised file type ({info.mime})")
    if tool == "compress":
        return compress.compress(info, reporter.progress)
    if tool == "metadata":
        if args.strip:
            return metadata.strip(info)
        tags = metadata.read(info)
        if not args.all:
            tags = metadata.embedded_only(tags)
        width = max((len(f"{g}:{n}") for g, n, _ in tags), default=0)
        for group, name, value in tags:
            key = f"{group}:{name}" if group else name
            print(f"{key:<{width}}  {value}")
        return Result()

    if info.kind == "pdf" and tool in ("redact", "annotate"):
        if tool == "redact":
            if not args.rect:
                raise RingConvertError("redact needs at least one --rect")
            return pdf.redact(path, args.rect)
        return pdf.annotate(path, _load_shapes(args.shapes))
    if info.kind != "image":
        raise RingConvertError(f"{tool} works on images" + (" and PDFs" if tool in ("redact", "annotate") else ""))

    if tool == "crop":
        if not args.box and not args.ratio:
            raise RingConvertError("crop needs --box X,Y,W,H or --ratio W:H")
        return imageops.crop(path, args.box, args.ratio)
    if tool == "edit":
        w, h = args.resize or (None, None)
        params = imageops.EditParams(
            rotate=args.rotate % 360, flip_h=args.flip_h, flip_v=args.flip_v, width=w, height=h,
            brightness=args.brightness, contrast=args.contrast, saturation=args.saturation)
        return imageops.edit(path, params)
    if tool == "add_bg":
        return imageops.add_background(path, args.background, args.padding, args.corners,
                                       args.shadow, args.ratio or "auto")
    if tool == "annotate":
        return imageops.annotate(path, _load_shapes(args.shapes))
    if tool == "redact":
        if not args.rect:
            raise RingConvertError("redact needs at least one --rect")
        return imageops.redact(path, [r[1:] for r in args.rect])
    raise RingConvertError(f"Unknown tool {tool!r}")


def _load_shapes(path: Path | None) -> list[dict]:
    if not path:
        raise RingConvertError("annotate needs --shapes FILE.json")
    try:
        shapes = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise RingConvertError(f"Can't read {path}: {exc}") from exc
    if not isinstance(shapes, list):
        raise RingConvertError("--shapes must contain a JSON list")
    return shapes


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.check:
        return show_check()
    if not args.files:
        parser.error("no input files")
    if not (args.to or args.tool or args.list):
        parser.error("choose --to FORMAT, --tool TOOL or --list")

    tool = None
    if args.tool:
        tool = TOOL_ALIASES.get(args.tool.lower(), args.tool.lower().replace("-", "_"))
        if tool not in ALL_TOOLS:
            parser.error(f"unknown tool {args.tool!r}")

    reporter = Reporter(args.quiet)
    failed = 0
    for path in args.files:
        try:
            if not path.is_file():
                raise RingConvertError("no such file")
            if args.list:
                show_list(path)
                continue
            if tool:
                result = run_tool(path, tool, args, reporter)
            else:
                result = convert(path, args.to, reporter.progress)
            reporter.done(result)
        except Exception as exc:
            reporter.done(Result())
            detail = str(exc) if isinstance(exc, RingConvertError) else f"{type(exc).__name__}: {exc}"
            print(f"ringconvert: {path.name}: {detail}", file=sys.stderr)
            failed += 1
        except KeyboardInterrupt:
            print(file=sys.stderr)
            return 130
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
