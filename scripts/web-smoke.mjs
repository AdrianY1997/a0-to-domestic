// Headless smoke test of the web local mode (SPEC 17bis): loads Pyodide + the a0td bundle in
// Chromium exactly as the worker will and drives a0td.web.bridge (ingest -> plan -> render).
//
//   node scripts/web-smoke.mjs [--site frontend/public/py] [--fixtures fixtures] [--port 0]
//
// Needs the bundle and Pyodide vendored first (tools/build_web_bundle.py, tools/vendor_pyodide.py)
// and Playwright (run `scripts/web-smoke.sh` to do all of it in Docker). PW_DIR may point to a
// folder where `playwright` is installed. Exit code 1 if any step fails.
import { createServer } from 'node:http';
import { readFile, stat } from 'node:fs/promises';
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const arg = (name, dflt) => {
  const i = process.argv.indexOf(`--${name}`);
  return i > 0 ? process.argv[i + 1] : dflt;
};
const site = path.resolve(arg('site', path.join(here, '..', 'frontend', 'public', 'py')));
const fixtures = path.resolve(arg('fixtures', path.join(here, '..', 'fixtures')));
const timeoutMs = Number(arg('timeout', '900')) * 1000;
const evalFile = arg('eval', null); // run this Python file instead of the smoke steps (debugging)

const requireFrom = createRequire(process.env.PW_DIR ? path.join(process.env.PW_DIR, '/') : import.meta.url);
const { chromium } = requireFrom('playwright');

const MIME = {
  '.html': 'text/html', '.js': 'text/javascript', '.mjs': 'text/javascript', '.json': 'application/json',
  '.wasm': 'application/wasm', '.zip': 'application/zip', '.whl': 'application/octet-stream',
};

const PAGE = `<!doctype html><meta charset=utf-8><title>web smoke</title><script type=module>
window.RESULT = { done: false, log: [] };
const log = (m) => { RESULT.log.push(m); };
try {
  const t0 = performance.now();
  const man = await (await fetch('/py/manifest.json')).json();
  const base = '/py/' + man.pyodide.path;
  const { loadPyodide } = await import(base + 'pyodide.mjs');
  const py = await loadPyodide({ indexURL: base });
  log('core ' + Math.round(performance.now() - t0) + ' ms');
  await py.loadPackage(man.packages);
  log('packages ' + Math.round(performance.now() - t0) + ' ms');
  window.progressEvents = [];
  window.jsProgress = (stage, pct, key, params) => { progressEvents.push([stage, pct, key, params]); };
  py.globals.set('extras_json', JSON.stringify(man.extras));
  py.globals.set('base_url', new URL(base, location.href).href);
  await py.runPythonAsync(\`
import json, micropip
for e in json.loads(extras_json):
    try:
        await micropip.install(base_url + e['wheel'], deps=False)
    except Exception as exc:
        print('extra failed', e['name'], exc)
\`);
  log('extras ' + Math.round(performance.now() - t0) + ' ms');
  const zip = await (await fetch('/py/' + man.bundle.file)).arrayBuffer();
  py.unpackArchive(zip, 'zip', { extractDir: '/a0td-src' });
  py.runPython("import sys; sys.path.insert(0, '/a0td-src')");
  py.FS.mkdirTree('/in');
  window.pyodide = py;
  RESULT.boot_ms = Math.round(performance.now() - t0);
  RESULT.boot = true;
} catch (e) {
  RESULT.error = String(e && e.stack || e);
}
RESULT.done = true;
</script>`;

const server = createServer(async (req, res) => {
  const url = decodeURIComponent((req.url ?? '/').split('?')[0]);
  let file = null;
  if (url === '/' || url === '/index.html') {
    res.writeHead(200, { 'content-type': 'text/html' });
    res.end(PAGE);
    return;
  }
  if (url.startsWith('/py/')) file = path.join(site, url.slice(4));
  else if (url.startsWith('/fixtures/')) file = path.join(fixtures, url.slice(10));
  try {
    if (!file || !(await stat(file)).isFile()) throw new Error('nf');
    res.writeHead(200, { 'content-type': MIME[path.extname(file)] ?? 'application/octet-stream' });
    res.end(await readFile(file));
  } catch {
    res.writeHead(404);
    res.end();
  }
});
await new Promise((r) => server.listen(Number(arg('port', '0')), '127.0.0.1', r));
const origin = `http://127.0.0.1:${server.address().port}`;

// ---- the Python side of the test: a0td.web.bridge as the worker uses it ----------------------
const FIXTURES = [
  'a0_vector_grid.pdf', 'photo_nodpi.jpg', 'cmyk.jpg', 'transparent.png', 'scan_a0_200dpi_g4.tif',
  'multipage.tif', 'svg_physical.svg', 'svg_no_units.svg', 'xps_a4.xps', 'dxf_model_plan.dxf',
  'photo_150dpi.heic', 'photo_nodpi.avif', 'encrypted.pdf', 'docx_simple.docx', 'layers_ocg.pdf',
];
const SMOKE_PY = String.raw`
import json, time, traceback
import js
from pathlib import Path
import pymupdf
from a0td.web import bridge
from a0td.imaging import backend_name

bridge.configure('/a0td')
steps, fails = {}, []
M5 = {"top": 5, "right": 5, "bottom": 5, "left": 5}

def step(name):
    def deco(fn):
        t = time.perf_counter()
        try:
            detail = fn()
            steps[name] = {"ok": True, "s": round(time.perf_counter() - t, 2), "detail": detail}
        except BaseException as exc:
            fails.append(name)
            steps[name] = {"ok": False, "s": round(time.perf_counter() - t, 2),
                           "error": repr(exc)[:400], "tb": traceback.format_exc()[-1200:]}
        return fn
    return deco

def need(cond, what):
    if not cond:
        raise AssertionError(what)

def ingest(name, **kw):
    return json.loads(bridge.ingest('/in/' + name, name, progress=js.jsProgress, **kw))

def settings(doc, **extra):
    return json.dumps({"document_id": doc["id"], "target": {"margins_mm": M5}, **extra})

@step('info')
def _():
    info = json.loads(bridge.info())
    by = {c["id"]: c["available"] for c in info["converters"]}
    need(backend_name() == "pillow", "imaging backend: " + backend_name())
    need(by["pdf"] and by["raster"] and by["svg"] and by["xps"], by)
    need(not by["office"] and not by["postscript"], by)
    need(info["local"] is True, info)
    return {"converters": by, "limits": info["limits"], "imaging": info["imaging"]}

@step('catalogs')
def _():
    need(len(json.loads(bridge.papers())) > 10, "papers")
    need(any(p["id"] == "builtin-generic" for p in json.loads(bridge.printers())), "printers")
    need(len(json.loads(bridge.presets())) > 0, "presets")
    need(bridge.calibration_pdf("A4", "en")[:5] == b"%PDF-", "calibration pdf")

vector = {}

@step('vector_ingest_plan_render_22_pages')
def _():
    doc = ingest('a0_vector_grid.pdf')
    need(doc["status"] == "ready" and doc["page_count"] == 1, doc["status"])
    vector["doc"] = doc
    s = settings(doc)
    plan = json.loads(bridge.plan(s, 0, True, None))
    need((plan["grid"]["cols"], plan["grid"]["rows"]) == (3, 7), plan["grid"])
    need(plan["counts"]["printed"] == 21, plan["counts"])
    png = bridge.sheet_preview(s, 0, "A1", 400)
    need(bytes(png)[:4] == b"\x89PNG", "sheet preview is not a PNG")
    need(bytes(bridge.page_preview(doc["id"], 0, 500))[:4] == b"\x89PNG", "page preview")
    n0 = len(js.progressEvents)
    res = json.loads(bridge.render(s, None, False, js.jsProgress))
    need(len(js.progressEvents) > n0, "no progress events reached JS")
    last = js.progressEvents[len(js.progressEvents) - 1].to_py()
    need(isinstance(last[3], dict), "params did not arrive as a plain object: " + repr(last))
    (out,) = res["outputs"]
    with pymupdf.open(out["path"]) as pdf:
        need(pdf.page_count == 22, pdf.page_count)
        sizes = {(round(p.rect.width), round(p.rect.height)) for p in pdf}
        need(sizes == {(842, 595)}, sizes)
    need(bytes(bridge.output_page_thumb(res["job_id"], out["name"], 5, 200))[1:4] == b"PNG", "output thumb")
    return {"pages": 22, "bytes": out["size_bytes"], "progress_events": len(js.progressEvents)}

@step('vector_test_sheet_and_png_zip')
def _():
    doc = vector["doc"]
    only = json.loads(bridge.render(settings(doc), None, True))
    need(only["outputs"][0]["pages"] == 1, only["outputs"])
    z = json.loads(bridge.render(settings(doc, output={"formats": ["png_zip"], "dpi": 150}), None, False))
    need(z["outputs"][0]["kind"] == "zip", z["outputs"])

@step('vector_grayscale_rasterized_without_ghostscript')
def _():
    res = json.loads(bridge.render(settings(vector["doc"], color={"mode": "grayscale"}), None, True))
    need(("LOCAL_LIMIT", "gray_vector") in [(w["code"], w["params"].get("limit")) for w in res["warnings"]], res["warnings"])

@step('dpi_is_capped_with_local_limit')
def _():
    res = json.loads(bridge.render(settings(vector["doc"], output={"dpi": 600, "rasterize": True}), None, True))
    need([w["code"] for w in res["warnings"]] == ["LOCAL_LIMIT"], res["warnings"])

@step('layers_preview')
def _():
    doc = ingest('layers_ocg.pdf')
    layers = doc["pages"][0]["layers"]
    need(len(layers) == 3, layers)
    need(bytes(bridge.page_preview(doc["id"], 0, 300, [layers[0]["name"]], None))[:4] == b"\x89PNG", "layered preview")

@step('raster_cmyk_jpeg_icc_warning')
def _():
    doc = ingest('cmyk.jpg')
    need(doc["status"] == "ready", doc)
    need("ICC_IGNORED" in [w["code"] for w in doc["warnings"]], doc["warnings"])

@step('raster_png_transparent_and_multipage_tiff')
def _():
    need(ingest('transparent.png')["status"] == "ready", "png")
    multi = ingest('multipage.tif')
    need(multi["status"] == "ready" and multi["page_count"] == 3, multi["status"])

@step('raster_photo_ingest_size_plan_render')
def _():
    doc = ingest('photo_nodpi.jpg')
    need(doc["status"] == "ready", doc.get("error_code"))
    page = json.loads(bridge.set_physical_size(doc["id"], 0, dpi=150))
    need(page["dpi"] == 150, page)
    s = settings(doc, scale={"mode": "percent", "percent": 30})
    plan = json.loads(bridge.plan(s, 0, False, None))
    res = json.loads(bridge.render(s, None, False, js.jsProgress))
    with pymupdf.open(res["outputs"][0]["path"]) as pdf:
        need(pdf.page_count == plan["counts"]["printed"] + 1, (pdf.page_count, plan["counts"]))
    bw = json.loads(bridge.render(settings(doc, scale={"mode": "percent", "percent": 30}, color={"mode": "bw", "whiten_background": True}), None, True))
    need(bw["outputs"][0]["kind"] == "pdf", bw)
    return {"printed": plan["counts"]["printed"], "px": page["px"]}

@step('raster_g4_scan_a0')
def _():
    doc = ingest('scan_a0_200dpi_g4.tif')
    need(doc["status"] == "ready", (doc["status"], doc.get("error_code"), doc.get("error_params")))
    return {"px": doc["pages"][0]["px"]}

@step('svg_physical_and_unitless')
def _():
    a = ingest('svg_physical.svg')
    need(a["status"] == "ready" and abs(a["pages"][0]["width_mm"] - 200) < 0.2, a["pages"])
    b = ingest('svg_no_units.svg')
    need(b["status"] == "ready", b)
    need("PHYSICAL_SIZE_ASSUMED" in [w["code"] for w in b["pages"][0]["warnings"]], b["pages"][0]["warnings"])

@step('xps')
def _():
    d = ingest('xps_a4.xps')
    need(d["status"] == "ready" and abs(d["pages"][0]["width_mm"] - 297) < 0.5, d["status"])

@step('dxf_via_ezdxf')
def _():
    d = ingest('dxf_model_plan.dxf', options={"cad": {"scale": 50}})
    need(d["status"] == "ready", (d["status"], d.get("error_code"), d.get("error_detail")))
    cad = json.loads(bridge.cad_info(d["id"]))
    need(cad["scale"] == 50 and cad["layouts"], cad)
    return {"w": d["pages"][0]["width_mm"]}

@step('heic_ok_avif_reported_unavailable')
def _():
    a = ingest('photo_150dpi.heic')
    need(a["status"] == "ready", a.get("error_detail"))
    b = ingest('photo_nodpi.avif')  # Pyodide's libheif has no AV1 decoder
    need(b["status"] == "failed" and b["error_code"] == "FORMAT_NOT_AVAILABLE_IN_IMAGE", b.get("error_code"))

@step('encrypted_pdf_password_flow')
def _():
    w = ingest('encrypted.pdf')
    need((w["status"], w["error_code"]) == ("password_required", "PASSWORD_REQUIRED"), w["status"])
    ok = json.loads(bridge.ingest('/in/encrypted.pdf', 'encrypted.pdf', 'secret', {"doc_id": w["id"]}))
    need(ok["status"] == "ready" and ok["id"] == w["id"], ok["status"])

@step('subprocess_format_reported_unavailable')
def _():
    d = ingest('docx_simple.docx')
    need((d["status"], d["error_code"]) == ("failed", "FORMAT_NOT_AVAILABLE_IN_IMAGE"), d)

@step('errors_are_problem_json')
def _():
    try:
        bridge.plan('{"document_id": "nope"}')
    except bridge.BridgeError as exc:
        body = json.loads(str(exc))
        need(body["code"] == "INVALID_SETTINGS" and body["status"] == 422, body)
    else:
        raise AssertionError("no error raised")

json.dumps({"steps": steps, "fails": fails})
`;

const browser = await chromium.launch();
const page = await browser.newPage();
const consoleErrors = [];
page.on('console', (m) => {
  if (m.type() === 'error') consoleErrors.push(m.text().slice(0, 300));
  else if (evalFile) console.log('[page]', m.text().slice(0, 300));
});
page.on('pageerror', (e) => consoleErrors.push(String(e).slice(0, 300)));
let exitCode = 0;
try {
  await page.goto(origin);
  await page.waitForFunction('window.RESULT && window.RESULT.done', null, { timeout: timeoutMs });
  const boot = await page.evaluate('window.RESULT');
  console.log('boot:', JSON.stringify({ ms: boot.boot_ms, log: boot.log }));
  if (!boot.boot) throw new Error('Pyodide did not start: ' + boot.error);

  // copy the fixtures into Pyodide's in-memory FS (bytes travel as base64 to keep evaluate simple)
  for (const name of FIXTURES) {
    const data = (await readFile(path.join(fixtures, name))).toString('base64');
    await page.evaluate(([n, b64]) => {
      const bin = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
      window.pyodide.FS.writeFile('/in/' + n, bin);
    }, [name, data]);
  }
  if (evalFile) {
    const out = await page.evaluate((code) => window.pyodide.runPythonAsync(code), await readFile(evalFile, 'utf8'));
    console.log(String(out));
    process.exit(0);
  }
  const raw = await page.evaluate((code) => window.pyodide.runPythonAsync(code), SMOKE_PY);
  const result = JSON.parse(raw);
  for (const [name, s] of Object.entries(result.steps)) {
    const mark = s.ok ? 'PASS' : 'FAIL';
    console.log(`${mark} ${name} (${s.s}s)` + (s.ok ? (s.detail ? ' ' + JSON.stringify(s.detail) : '') : '\n  ' + s.error + '\n' + s.tb));
  }
  if (result.fails.length) exitCode = 1;
  console.log(result.fails.length ? `FAILED: ${result.fails.join(', ')}` : 'web smoke: all steps passed');
} catch (e) {
  console.error('web smoke crashed:', e);
  exitCode = 1;
} finally {
  if (consoleErrors.length) console.log('console errors:', consoleErrors.slice(0, 10));
  await browser.close();
  server.close();
}
process.exit(exitCode);
