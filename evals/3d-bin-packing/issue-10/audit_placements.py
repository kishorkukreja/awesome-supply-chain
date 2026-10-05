"""Independent check of a 3D placement dump against the shipment, in real cm.

Usage: python3 audit_placements.py shipment.csv placements.json [PO1,PO2]

The optional third argument lists POs that may span containers (an oversize PO
the user agreed to split). Every other PO must sit in one container.

placements.json is a list of {"c", "po", "sku", "pos": [x, y, z], "size": [l, w, h]}
in cm, z up. Checks carton counts per line, containment, overlap, upright
cartons kept upright (half-cm rounding allowed), and support under each base.
Fault-injected with an overlap, a tipped carton, a missing carton and an
out-of-bounds carton, and it flags all four.
"""
import json, csv, sys, collections
L, W, H = 1203.5, 235.2, 269.5
ship = list(csv.DictReader(open(sys.argv[1])))
pl = json.load(open(sys.argv[2]))
need = {(r['po'], r['sku']): (sorted([float(r['length_cm']), float(r['width_cm']), float(r['height_cm'])]), int(r['cartons']), r['this_side_up'] == '1', float(r['height_cm'])) for r in ship}
got = collections.Counter((p['po'], str(p['sku'])) for p in pl)
bad = collections.Counter()
for k, (dims, n, up, h) in need.items():
    if got[k] != n: bad[f'count mismatch {k}: {got[k]} vs {n}'] += 1
po_cont = collections.defaultdict(set)
by_c = collections.defaultdict(list)
for p in pl:
    po_cont[p['po']].add(p['c']); by_c[p['c']].append(p)
    dims, n, up, h = need[(p['po'], str(p['sku']))]
    if sorted(p['size']) < [d - 1e-6 for d in dims] or any(s < d - 1e-6 for s, d in zip(sorted(p['size']), dims)):
        bad['placed smaller than real carton'] += 1
    if up and abs(p['size'][2] - h) > 0.51: bad['this_side_up tipped'] += 1
    x, y, z = p['pos']; l, w, hh = p['size']
    if x < -1e-6 or y < -1e-6 or z < -1e-6 or x + l > L + 1e-6 or y + w > W + 1e-6 or z + hh > H + 1e-6: bad['outside container (real cm)'] += 1
allowed_split = set(sys.argv[3].split(',')) if len(sys.argv) > 3 and sys.argv[3] else set()
bad['POs split'] = sum(1 for po, s in po_cont.items() if len(s) > 1 and po not in allowed_split)
minsup = 1.0
for c, its in by_c.items():
    its.sort(key=lambda p: p['pos'][0])
    for i, a in enumerate(its):
        ax, ay, az = a['pos']; al, aw, ah = a['size']
        for b in its[i + 1:]:
            if b['pos'][0] >= ax + al - 1e-6: break
            bx, by_, bz = b['pos']; bl, bw, bh = b['size']
            if not (ay + aw <= by_ + 1e-6 or by_ + bw <= ay + 1e-6 or az + ah <= bz + 1e-6 or bz + bh <= az + 1e-6):
                bad['overlapping pairs'] += 1
        if az > 1e-6:
            area = sum(max(0, min(ax + al, b['pos'][0] + b['size'][0]) - max(ax, b['pos'][0])) * max(0, min(ay + aw, b['pos'][1] + b['size'][1]) - max(ay, b['pos'][1]))
                       for b in its if abs(b['pos'][2] + b['size'][2] - az) < 1e-6)
            minsup = min(minsup, area / (al * aw))
    v = sum(p['size'][0] * p['size'][1] * p['size'][2] for p in its) / 1e6
    print(f"  container {c}: {len(its)} cartons, {v:.1f} m3 placed")
print(f"  min support ratio: {minsup:.2f}")
bad = {k: v for k, v in bad.items() if v}
print("  VALID" if not bad else f"  INVALID: {bad}")
