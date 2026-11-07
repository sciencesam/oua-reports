"""Small inline-SVG chart helpers for HTML reports (no dependencies).

Every chart is a string of SVG that can be dropped into an HTML page. Marks
carry <title> elements so browsers show a tooltip on hover, and every chart
should be accompanied by a table with the same numbers.

Colors follow a validated categorical palette (blue, orange, aqua) plus a
de-emphasis gray for context series; sequential ramps use one hue.
"""

import html
import math

series_colors = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]
context_gray = "#c3c2b7"
ink = "#0b0b0b"
ink_secondary = "#52514e"
muted = "#898781"
gridline = "#e1e0d9"
axis = "#c3c2b7"
surface = "#fcfcfb"
blue_ramp = ["#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7",
             "#3987e5", "#2a78d6", "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b"]
font = "font-family='system-ui, -apple-system, Segoe UI, sans-serif'"


def esc(text):
    return html.escape(str(text), quote=True)


def nice_ticks(vmax, count=5):
    if vmax <= 0:
        return [0]
    raw = vmax / count
    magnitude = 10 ** math.floor(math.log10(raw))
    for step in (1, 2, 2.5, 5, 10):
        if raw <= step * magnitude:
            break
    step = step * magnitude
    ticks = []
    value = 0
    while True:
        ticks.append(round(value, 6))
        if value >= vmax - 1e-9:
            break
        value += step
    return ticks


def text(x, y, label, size=12, color=ink_secondary, anchor="start", weight="normal", extra=""):
    return (f"<text x='{x:.1f}' y='{y:.1f}' font-size='{size}' fill='{color}' text-anchor='{anchor}' "
            f"font-weight='{weight}' {font} {extra}>{esc(label)}</text>")


def legend(items, x, y, max_width=700):
    """items: [(label, color)] laid out horizontally, wrapping upward onto
    extra rows when the row would exceed max_width."""
    rows, row, width = [], [], 0
    for label, color in items:
        w = 17 + 7.2 * len(label) + 22
        if row and width + w > max_width - x:
            rows.append(row); row, width = [], 0
        row.append((label, color)); width += w
    rows.append(row)
    out = []
    for i, row in enumerate(reversed(rows)):
        yy = y - 18 * i; cursor = x
        for label, color in row:
            out.append(f"<rect x='{cursor}' y='{yy - 9}' width='12' height='12' rx='2' fill='{color}'/>")
            out.append(text(cursor + 17, yy + 1, label, size=12))
            cursor += 17 + 7.2 * len(label) + 22
    return "".join(out)


def grouped_bars(categories, series, title="", ylabel="", width=720, height=300,
                 value_format="{:.0f}", colors=None, note="", ylim=None):
    """series: list of (name, values, color-or-None). Vertical grouped bars.
    Values are shown in tooltips and the extreme of each series is labeled."""
    colors = colors or series_colors
    left, right, top, bottom = 52, 16, 44, 56
    plot_w, plot_h = width - left - right, height - top - bottom
    vmax = ylim or max((v for _, values, _ in series for v in values if v is not None), default=1) or 1
    ticks = nice_ticks(vmax)
    vmax = ticks[-1]
    n_cat, n_ser = len(categories), len(series)
    slot = plot_w / max(n_cat, 1)
    bar_w = max(3, (slot * 0.78 - 2 * (n_ser - 1)) / n_ser)
    out = [f"<svg viewBox='0 0 {width} {height}' width='{width}' height='{height}' role='img' aria-label='{esc(title)}'>",
           f"<rect width='{width}' height='{height}' fill='{surface}'/>"]
    if title:
        out.append(text(left, 20, title, size=14, color=ink, weight="600"))
    if ylabel:
        out.append(text(left, 36, ylabel, size=11, color=muted))
    for tick in ticks:
        y = top + plot_h - plot_h * tick / vmax
        out.append(f"<line x1='{left}' x2='{left + plot_w}' y1='{y:.1f}' y2='{y:.1f}' stroke='{gridline}' stroke-width='1'/>")
        out.append(text(left - 6, y + 4, value_format.format(tick), size=11, color=muted, anchor="end"))
    out.append(f"<line x1='{left}' x2='{left + plot_w}' y1='{top + plot_h}' y2='{top + plot_h}' stroke='{axis}'/>")
    for c, category in enumerate(categories):
        x0 = left + c * slot + slot * 0.11
        for s, (name, values, color) in enumerate(series):
            value = values[c]
            if value is None:
                continue
            h = plot_h * value / vmax
            x = x0 + s * (bar_w + 2)
            y = top + plot_h - h
            fill = color or colors[s % len(colors)]
            out.append(f"<rect x='{x:.1f}' y='{y:.1f}' width='{bar_w:.1f}' height='{h:.1f}' rx='3' fill='{fill}'>"
                       f"<title>{esc(name)} · {esc(category)}: {esc(value_format.format(value))}</title></rect>")
        out.append(text(left + c * slot + slot / 2, top + plot_h + 16, category, size=11, anchor="middle"))
    # label the maximum of each series
    for s, (name, values, color) in enumerate(series):
        present = [(v, i) for i, v in enumerate(values) if v is not None]
        if not present:
            continue
        value, c = max(present)
        x = left + c * slot + slot * 0.11 + s * (bar_w + 2) + bar_w / 2
        y = top + plot_h - plot_h * value / vmax - 4
        out.append(text(x, y, value_format.format(value), size=10, anchor="middle"))
    out.append(legend([(name, color or colors[s % len(colors)]) for s, (name, _, color) in enumerate(series)],
                      left, height - 10))
    if note:
        out.append(text(width - right, height - 10, note, size=10, color=muted, anchor="end"))
    out.append("</svg>")
    return "".join(out)


def dumbbell(rows, title="", xlabel="", width=720, row_h=14, before_label="before", after_label="after",
             value_format="{:.0f}", vmax=None):
    """rows: [(label, before, after, tooltip)] ordered top to bottom.
    A light and a dark step of one hue, joined by a hairline."""
    left, right, top, bottom = 290, 20, 44, 58
    height = top + bottom + row_h * len(rows)
    plot_w = width - left - right
    vmax = vmax or max((max(b, a) for _, b, a, _ in rows), default=1) or 1
    ticks = nice_ticks(vmax)
    vmax = ticks[-1]
    sx = lambda v: left + plot_w * v / vmax
    light, dark = "#86b6ef", "#1c5cab"
    out = [f"<svg viewBox='0 0 {width} {height}' width='{width}' height='{height}' role='img' aria-label='{esc(title)}'>",
           f"<rect width='{width}' height='{height}' fill='{surface}'/>"]
    if title:
        out.append(text(left, 20, title, size=14, color=ink, weight="600"))
    for tick in ticks:
        x = sx(tick)
        out.append(f"<line x1='{x:.1f}' x2='{x:.1f}' y1='{top}' y2='{top + row_h * len(rows)}' stroke='{gridline}'/>")
        out.append(text(x, top + row_h * len(rows) + 14, value_format.format(tick), size=10, color=muted, anchor="middle"))
    for i, (label, before, after, tip) in enumerate(rows):
        y = top + row_h * i + row_h / 2
        out.append(text(left - 8, y + 3.5, label, size=10, anchor="end"))
        x1, x2 = sx(before), sx(after)
        color = dark if after >= before else "#e34948"
        out.append(f"<line x1='{x1:.1f}' x2='{x2:.1f}' y1='{y:.1f}' y2='{y:.1f}' stroke='{color}' stroke-width='2' opacity='0.6'/>")
        out.append(f"<circle cx='{x1:.1f}' cy='{y:.1f}' r='4' fill='{light}' stroke='{surface}' stroke-width='1.5'><title>{esc(tip)}</title></circle>")
        out.append(f"<circle cx='{x2:.1f}' cy='{y:.1f}' r='4' fill='{color}' stroke='{surface}' stroke-width='1.5'><title>{esc(tip)}</title></circle>")
    if xlabel:
        out.append(text(left, height - 26, xlabel, size=11, color=muted))
    out.append(legend([(before_label, light), (after_label + " (up or same)", dark), (after_label + " (down)", "#e34948")],
                      12, height - 8, max_width=width - 12))     # one row along the bottom, clear of the axis
    out.append("</svg>")
    return "".join(out)


def strips(groups, title="", xlabel="", width=760, row_h=34, control_label="matched controls",
           value_format="{:+.0f}", symmetric=True, clip=None):
    """groups: [(label, values, control_median_or_None)]. One horizontal dot
    strip per group with a median tick; the control median is a gray tick.
    With clip=N, values beyond ±N are drawn at the edge (tooltip keeps the value)."""
    left, right, top, bottom = 250, 20, 44, 58
    height = top + bottom + row_h * len(groups)
    plot_w = width - left - right
    clamp = (lambda v: max(-clip, min(clip, v))) if clip else (lambda v: v)
    all_values = [clamp(v) for _, values, c in groups for v in values] + [clamp(c) for _, _, c in groups if c is not None]
    if not all_values:
        all_values = [0]
    lo, hi = min(all_values), max(all_values)
    if symmetric:
        span = max(abs(lo), abs(hi), 1)
        lo, hi = -span, span
    lo, hi = math.floor(lo), math.ceil(hi) or 1
    sx = lambda v: left + plot_w * (v - lo) / (hi - lo)
    out = [f"<svg viewBox='0 0 {width} {height}' width='{width}' height='{height}' role='img' aria-label='{esc(title)}'>",
           f"<rect width='{width}' height='{height}' fill='{surface}'/>"]
    if title:
        out.append(text(left, 20, title, size=14, color=ink, weight="600"))
    for tick in sorted(set(nice_ticks(hi) + [-t for t in nice_ticks(-lo)])):
        if tick < lo or tick > hi:
            continue
        x = sx(tick)
        stroke = axis if tick == 0 else gridline
        out.append(f"<line x1='{x:.1f}' x2='{x:.1f}' y1='{top}' y2='{top + row_h * len(groups)}' stroke='{stroke}'/>")
        out.append(text(x, top + row_h * len(groups) + 14, value_format.format(tick), size=10, color=muted, anchor="middle"))
    for i, (label, values, control) in enumerate(groups):
        y0 = top + row_h * i
        yc = y0 + row_h / 2
        out.append(text(left - 8, yc + 4, f"{label} (n={len(values)})", size=11, anchor="end"))
        counts = {}
        for j, value in enumerate(sorted(values)):
            shown = clamp(value)
            k = counts.get(shown, 0)
            counts[shown] = k + 1
            jitter = ((k % 7) - 3) * 3.2
            out.append(f"<circle cx='{sx(shown):.1f}' cy='{yc + jitter:.1f}' r='3.5' fill='{series_colors[0]}' "
                       f"fill-opacity='0.55' stroke='{surface}' stroke-width='1'><title>{esc(label)}: {esc(value_format.format(value))}</title></circle>")
        if values:
            median = sorted(values)[len(values) // 2] if len(values) % 2 else \
                (sorted(values)[len(values) // 2 - 1] + sorted(values)[len(values) // 2]) / 2
            x = sx(clamp(median))
            out.append(f"<line x1='{x:.1f}' x2='{x:.1f}' y1='{y0 + 4}' y2='{y0 + row_h - 4}' stroke='{ink}' stroke-width='2'>"
                       f"<title>median {esc(value_format.format(median))}</title></line>")
        if control is not None:
            x = sx(clamp(control))
            out.append(f"<line x1='{x:.1f}' x2='{x:.1f}' y1='{y0 + 6}' y2='{y0 + row_h - 6}' stroke='{muted}' stroke-width='2' stroke-dasharray='3 2'>"
                       f"<title>{esc(control_label)} median {esc(value_format.format(control))}</title></line>")
    if xlabel:
        out.append(text(left, height - 22, xlabel, size=11, color=muted))
    out.append(text(left, height - 6, f"black tick = group median · gray dashed = {control_label} median",
                    size=10, color=muted))
    out.append("</svg>")
    return "".join(out)


def heatmap(row_labels, col_labels, matrix, title="", row_title="", col_title="", width=640, cell=80):
    """matrix[i][j] counts; sequential blue ramp, count printed in each cell."""
    left, top = 190, 70
    cell_h = 40
    height = top + cell_h * len(row_labels) + 30
    vmax = max((v for row in matrix for v in row), default=1) or 1
    out = [f"<svg viewBox='0 0 {width} {height}' width='{width}' height='{height}' role='img' aria-label='{esc(title)}'>",
           f"<rect width='{width}' height='{height}' fill='{surface}'/>"]
    if title:
        out.append(text(16, 20, title, size=14, color=ink, weight="600"))
    out.append(text(left, 40, col_title, size=11, color=muted))
    out.append(text(16, top - 6, row_title, size=11, color=muted))
    for j, label in enumerate(col_labels):
        out.append(text(left + j * cell + cell / 2, top - 6, label, size=10, anchor="middle"))
    for i, row_label in enumerate(row_labels):
        out.append(text(left - 8, top + i * cell_h + cell_h / 2 + 4, row_label, size=11, anchor="end"))
        for j, value in enumerate(matrix[i]):
            step = 0 if value == 0 else min(len(blue_ramp) - 1, int(1 + (len(blue_ramp) - 2) * value / vmax))
            fill = "#f0efec" if value == 0 else blue_ramp[step]
            color = ink if step < 7 else "#ffffff"
            x, y = left + j * cell, top + i * cell_h
            out.append(f"<rect x='{x + 1}' y='{y + 1}' width='{cell - 2}' height='{cell_h - 2}' rx='3' fill='{fill}'>"
                       f"<title>{esc(row_label)} → {esc(col_labels[j])}: {value}</title></rect>")
            if value:
                out.append(text(x + cell / 2, y + cell_h / 2 + 4, value, size=12, color=color, anchor="middle"))
    out.append("</svg>")
    return "".join(out)


def lines(series, title="", xlabel="", ylabel="", width=720, height=300, x_ticks=None,
          value_format="{:.0f}%", colors=None, x_labels=None):
    """series: [(name, [(x, y)], color-or-None)]. y is a percentage 0..100."""
    colors = colors or series_colors
    left, right, top, bottom = 52, 16, 44, 56
    plot_w, plot_h = width - left - right, height - top - bottom
    xs = [x for _, points, _ in series for x, _ in points]
    xmin, xmax = min(xs), max(xs)
    ymax = max((y for _, points, _ in series for _, y in points), default=100) or 100
    ticks = nice_ticks(ymax)
    ymax = ticks[-1]
    sx = lambda x: left + plot_w * (x - xmin) / (xmax - xmin or 1)
    sy = lambda y: top + plot_h - plot_h * y / ymax
    out = [f"<svg viewBox='0 0 {width} {height}' width='{width}' height='{height}' role='img' aria-label='{esc(title)}'>",
           f"<rect width='{width}' height='{height}' fill='{surface}'/>"]
    if title:
        out.append(text(left, 20, title, size=14, color=ink, weight="600"))
    if ylabel:
        out.append(text(left, 36, ylabel, size=11, color=muted))
    for tick in ticks:
        out.append(f"<line x1='{left}' x2='{left + plot_w}' y1='{sy(tick):.1f}' y2='{sy(tick):.1f}' stroke='{gridline}'/>")
        out.append(text(left - 6, sy(tick) + 4, value_format.format(tick), size=11, color=muted, anchor="end"))
    for tick in (x_ticks or [xmin, xmax]):
        label = x_labels.get(tick, f"{tick:g}") if x_labels else f"{tick:g}"
        out.append(text(sx(tick), top + plot_h + 16, label, size=10, color=muted, anchor="middle"))
    if xlabel:
        out.append(text(left + plot_w / 2, top + plot_h + 34, xlabel, size=11, color=muted, anchor="middle"))
    for s, (name, points, color) in enumerate(series):
        stroke = color or colors[s % len(colors)]
        path = " ".join(f"{'M' if i == 0 else 'L'}{sx(x):.1f},{sy(y):.1f}" for i, (x, y) in enumerate(points))
        out.append(f"<path d='{path}' fill='none' stroke='{stroke}' stroke-width='2'><title>{esc(name)}</title></path>")
        x, y = points[-1]
        out.append(text(sx(x) - 4, sy(y) - 6, value_format.format(y), size=10, anchor="end"))
    out.append(legend([(name, color or colors[s % len(colors)]) for s, (name, _, color) in enumerate(series)],
                      left, height - 10))
    out.append("</svg>")
    return "".join(out)


def stat_tiles(tiles):
    """tiles: [(label, value, delta_text)] rendered as an HTML KPI row."""
    out = ["<div class='tiles'>"]
    for label, value, delta in tiles:
        out.append("<div class='tile'>"
                   f"<div class='tile-label'>{esc(label)}</div>"
                   f"<div class='tile-value'>{esc(value)}</div>"
                   f"<div class='tile-delta'>{esc(delta)}</div></div>")
    out.append("</div>")
    return "".join(out)


def table(columns, rows, caption=""):
    out = ["<table>"]
    if caption:
        out.append(f"<caption>{esc(caption)}</caption>")
    out.append("<tr>" + "".join(f"<th>{esc(c)}</th>" for c in columns) + "</tr>")
    for row in rows:
        out.append("<tr>" + "".join(f"<td>{esc(v)}</td>" for v in row) + "</tr>")
    out.append("</table>")
    return "".join(out)


def mean_sd(groups, title="", ylabel="", width=720, height=320, value_format="{:.0f}", show_points=True):
    """groups: [(category, [(series_label, values, color)])]. For each series in
    each category: a mean marker with ±1 standard deviation whiskers, and the
    individual values as faint dots beside it."""
    import statistics
    left, right, top, bottom = 52, 16, 44, 56
    plot_w, plot_h = width - left - right, height - top - bottom
    all_values = [v for _, series in groups for _, values, _ in series for v in values]
    highs = [statistics.mean(values) + (statistics.pstdev(values) if len(values) > 1 else 0) for _, series in groups for _, values, _ in series if values]
    vmax = max(max(all_values, default=1), max(highs, default=1)) or 1
    ticks = nice_ticks(vmax)
    vmax = ticks[-1]
    sy = lambda v: top + plot_h - plot_h * v / vmax
    out = [f"<svg viewBox='0 0 {width} {height}' width='{width}' height='{height}' role='img' aria-label='{esc(title)}'>",
           f"<rect width='{width}' height='{height}' fill='{surface}'/>"]
    if title:
        out.append(text(left, 20, title, size=14, color=ink, weight="600"))
    if ylabel:
        out.append(text(left, 36, ylabel, size=11, color=muted))
    for tick in ticks:
        out.append(f"<line x1='{left}' x2='{left + plot_w}' y1='{sy(tick):.1f}' y2='{sy(tick):.1f}' stroke='{gridline}'/>")
        out.append(text(left - 6, sy(tick) + 4, value_format.format(tick), size=11, color=muted, anchor="end"))
    out.append(f"<line x1='{left}' x2='{left + plot_w}' y1='{top + plot_h}' y2='{top + plot_h}' stroke='{axis}'/>")
    slot = plot_w / max(len(groups), 1)
    legend_items = []
    for g, (category, series) in enumerate(groups):
        n_ser = len(series)
        for s, (label, values, color) in enumerate(series):
            color = color or series_colors[s % len(series_colors)]
            if (label, color) not in legend_items:
                legend_items.append((label, color))
            x = left + g * slot + slot * (s + 1) / (n_ser + 1)
            if not values:
                continue
            m = statistics.mean(values)
            sd = statistics.pstdev(values) if len(values) > 1 else 0
            if show_points:
                counts = {}
                for v in sorted(values):
                    k = counts.get(v, 0)
                    counts[v] = k + 1
                    out.append(f"<circle cx='{x + 14 + (k % 4) * 3.5:.1f}' cy='{sy(v):.1f}' r='2.6' fill='{color}' fill-opacity='0.35'>"
                               f"<title>{esc(category)} · {esc(label)}: {esc(value_format.format(v))}</title></circle>")
            y0, y1 = sy(max(0, m - sd)), sy(m + sd)
            out.append(f"<line x1='{x:.1f}' x2='{x:.1f}' y1='{y1:.1f}' y2='{y0:.1f}' stroke='{color}' stroke-width='2'/>")
            for y in (y0, y1):
                out.append(f"<line x1='{x - 6:.1f}' x2='{x + 6:.1f}' y1='{y:.1f}' y2='{y:.1f}' stroke='{color}' stroke-width='2'/>")
            out.append(f"<circle cx='{x:.1f}' cy='{sy(m):.1f}' r='5' fill='{color}' stroke='{surface}' stroke-width='1.5'>"
                       f"<title>{esc(category)} · {esc(label)}: mean {m:.1f}, sd {sd:.1f}, n={len(values)}</title></circle>")
            out.append(text(x - 9, sy(m) + 4, value_format.format(m), size=10, anchor="end"))
        out.append(text(left + g * slot + slot / 2, top + plot_h + 16, category, size=11, anchor="middle"))
    out.append(legend(legend_items, left, height - 10))
    out.append(text(width - right, height - 10, "marker = mean · whiskers = ±1 sd · dots = individual values", size=10, color=muted, anchor="end"))
    out.append("</svg>")
    return "".join(out)


def stacked_100(rows, width=720, bar_h=22, gap=14, colors=None, label_width=190):
    """rows: [(label, [(segment_label, count), ...])]. One horizontal 100%
    bar per row, segments in the given order, counts printed inside when
    they fit; a legend per row is unnecessary because segments are labeled."""
    colors = colors or ["#2a78d6", "#86b6ef", "#eb6834", "#f4b08a", "#1baf7a", "#a6dcc6", "#898781", "#c3c2b7"]
    height = 10 + len(rows) * (bar_h + gap)
    out = [f"<svg viewBox='0 0 {width} {height}' width='{width}' height='{height}' role='img'>",
           f"<rect width='{width}' height='{height}' fill='{surface}'/>"]
    plot_w = width - label_width - 16
    for i, (label, segments) in enumerate(rows):
        y = 10 + i * (bar_h + gap)
        total = sum(n for _, n in segments) or 1
        out.append(text(label_width - 10, y + bar_h / 2 + 4, label, size=11, anchor="end"))
        x = label_width
        for j, (seg, n) in enumerate(segments):
            w = plot_w * n / total
            if w <= 0:
                continue
            fill = colors[j % len(colors)]
            out.append(f"<rect x='{x:.1f}' y='{y}' width='{max(w - 2, 0):.1f}' height='{bar_h}' rx='3' fill='{fill}'><title>{esc(seg)}: {n} ({100 * n / total:.0f}%)</title></rect>")
            inner = f"{seg} {n}"
            if w > 7 * len(inner) + 8:
                out.append(text(x + 6, y + bar_h / 2 + 4, inner, size=10, color="#ffffff" if j % 2 == 0 else ink))
            elif w > 24:
                out.append(text(x + w / 2 - 1, y + bar_h / 2 + 4, str(n), size=10, color="#ffffff" if j % 2 == 0 else ink, anchor="middle"))
            x += w
        # segment labels that did not fit go under the bar
        small = [f"{seg} {n}" for seg, n in segments if plot_w * n / total <= 7 * len(f'{seg} {n}') + 8 and n > 0]
        if small:
            out.append(text(label_width, y + bar_h + 11, " · ".join(small), size=9, color=muted))
    out.append("</svg>")
    return "".join(out)


def hbars(items, width=720, bar_h=16, gap=6, label_width=230, color=None, vmax=None, value_format="{:d}"):
    """items: [(label, value)] as horizontal bars with the value at the end. One series, one hue."""
    color = color or series_colors[0]
    height = 8 + len(items) * (bar_h + gap)
    plot_w = width - label_width - 60
    vmax = vmax or max((v for _, v in items), default=1) or 1
    out = [f"<svg viewBox='0 0 {width} {height}' width='{width}' height='{height}' role='img'>", f"<rect width='{width}' height='{height}' fill='{surface}'/>"]
    for i, (label, value) in enumerate(items):
        y = 8 + i * (bar_h + gap)
        w = plot_w * value / vmax
        out.append(text(label_width - 10, y + bar_h / 2 + 4, label, size=11, anchor="end"))
        out.append(f"<rect x='{label_width}' y='{y}' width='{max(w, 1):.1f}' height='{bar_h}' rx='3' fill='{color}'><title>{esc(label)}: {value}</title></rect>")
        out.append(text(label_width + w + 6, y + bar_h / 2 + 4, value_format.format(value), size=11))
    out.append("</svg>")
    return "".join(out)


def overlaid_hist(categories, series, width=720, height=320, ylabel="% of group", value_format="{:.0f}%", colors=None, opacity=0.55):
    """series: [(name, values, color)] drawn as translucent overlapping bars per
    category (same x slot), so distributions can be compared directly."""
    colors = colors or series_colors
    left, right, top, bottom = 52, 16, 30, 76
    plot_w, plot_h = width - left - right, height - top - bottom
    vmax = max((v for _, values, _ in series for v in values if v is not None), default=1) or 1
    ticks = nice_ticks(vmax); vmax = ticks[-1]
    slot = plot_w / max(len(categories), 1)
    out = [f"<svg viewBox='0 0 {width} {height}' width='{width}' height='{height}' role='img'>", f"<rect width='{width}' height='{height}' fill='{surface}'/>"]
    if ylabel:
        out.append(text(left, 20, ylabel, size=11, color=muted))
    for tick in ticks:
        y = top + plot_h - plot_h * tick / vmax
        out.append(f"<line x1='{left}' x2='{left + plot_w}' y1='{y:.1f}' y2='{y:.1f}' stroke='{gridline}'/>")
        out.append(text(left - 6, y + 4, value_format.format(tick), size=11, color=muted, anchor="end"))
    out.append(f"<line x1='{left}' x2='{left + plot_w}' y1='{top + plot_h}' y2='{top + plot_h}' stroke='{axis}'/>")
    n = len(series)
    for c, category in enumerate(categories):
        for s, (name, values, color) in enumerate(series):
            v = values[c]
            if v is None: continue
            h = plot_h * v / vmax
            shrink = 0.78 - 0.1 * s                      # each later series slightly narrower so all stay visible
            w = slot * shrink; x = left + c * slot + (slot - w) / 2
            fill = color or colors[s % len(colors)]
            out.append(f"<rect x='{x:.1f}' y='{top + plot_h - h:.1f}' width='{w:.1f}' height='{h:.1f}' rx='2' fill='{fill}' fill-opacity='{opacity}' stroke='{fill}' stroke-width='1'>"
                       f"<title>{esc(name)} · {esc(category)}: {esc(value_format.format(v))}</title></rect>")
        out.append(text(left + c * slot + slot / 2, top + plot_h + 16, category, size=11, anchor="middle"))
    out.append(legend([(name, color or colors[s % len(colors)]) for s, (name, _, color) in enumerate(series)], left, height - 10))
    out.append("</svg>")
    return "".join(out)
