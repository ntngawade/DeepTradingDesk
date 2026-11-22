"""Offline marked-chart reader using the OCR component already built into Windows."""
import base64
import binascii
import json
import math
import os
from pathlib import Path
import re
import subprocess
import threading
import time

class LocalAIError(ValueError):
    pass

ROOT = Path(__file__).resolve().parent
BUSY = threading.Lock()
STATUS_CACHE = None
STATUS_TIME = 0
API_VERSION = 17
RAPID_ENGINE = None
RAPID_LOCK = threading.Lock()
SETUPS = {'Y': 'YIT', 'H': 'HYIT', 'HY': 'HYIT', 'Q': 'QIT', 'M': 'MIT', 'W': 'WIT', 'D': 'DIT'}
HIGHER_TIMEFRAMES = {'Y': 'Yearly', 'H': 'Half-yearly', 'HY': 'Half-yearly', 'Q': 'Quarterly', 'M': 'Monthly', 'W': 'Weekly', 'D': 'Daily'}
TIMEFRAMES = {'Y': 'Yearly', 'Q': 'Quarterly', 'M': 'Monthly', 'W': 'Weekly', 'D': 'Daily', 'H': 'Hourly'}
NUMERIC_TIMEFRAMES = {1, 2, 3, 5, 10, 15, 20, 30, 45, 60, 75, 90, 120, 125, 150, 180, 240}
NUMBER = r'(\d[\d,]*(?:\.\s*\d{1,4})?)(?![\w.,])'
LABELS = {'entry': r'(?:Entr[yv]|E(?=\s*[:=.-]))', 'sl': r'(?:Sto[po](?:[ -]?loss)?t?|S\.?L\.?)', 'target': r'(?:Target|TGT|T(?=\s*[:=.-]))'}
REVERSED_LABELS = {
    'entry': r'(?:E|Entry)',
    'sl': r'(?:SL|Stop(?:\s*[- ]?loss)?)',
    'target': r'(?:T|TGT|Target)',
}

def native_ocr(image=None):
    if os.name != 'nt':
        raise LocalAIError('The built-in reader currently supports this Windows computer only.')
    mode = 'read' if image else 'status'
    try:
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(ROOT/'chart-ocr.ps1'), '-Mode', mode],
                                input=json.dumps({'image': image}) if image else '', capture_output=True, text=True, encoding='utf-8',
                                timeout=40 if image else 15, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        data = json.loads(result.stdout.lstrip('\ufeff'))
        if result.returncode or not isinstance(data, dict) or data.get('error'):
            raise LocalAIError('Windows chart text recognition failed. Try a clearer screenshot or check Windows English OCR support.')
        return data
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        raise LocalAIError('The built-in chart reader could not finish. Retry with a smaller, clearer PNG/JPEG image.') from None

def rapid_ocr(image):
    """Run local RapidOCR and return the same normalized line format as Windows OCR."""
    global RAPID_ENGINE
    try:
        import numpy as np
        from PIL import Image, ImageEnhance, ImageFilter, ImageOps
        from rapidocr import RapidOCR
        raw = base64.b64decode(image, validate=True)
        with Image.open(__import__('io').BytesIO(raw)) as source:
            source = source.convert('RGB')
            width, height = source.size
            regions = [(0, 0, width, height, 'full')]
            # Price annotations are often tiny. Run overlapping enlarged
            # regions in addition to the overview and map each box back to
            # normalized full-image coordinates for the common parser.
            for yf in (0.0, 0.28, 0.56):
                for xf in (0.0, 0.42, 0.70):
                    left = int(width*xf)
                    top = int(height*yf)
                    crop_w = min(width-left, max(1, int(width*(0.30 if xf == 0.70 else 0.58))))
                    crop_h = min(height-top, max(1, int(height*0.42)))
                    rect = (left, top, left+crop_w, top+crop_h)
                    if rect != (0, 0, width, height) and crop_w > width*0.16 and crop_h > height*0.15:
                        regions.append((*rect, 'detail'))
            # TradeTiger and similar platforms render the instrument and
            # interval in a thin, small-font title strip. Scan overlapping
            # header tiles at higher magnification so ticker/TF are not lost
            # when the full chart OCR skips that tiny text.
            header_h = max(1, int(height*0.12))
            for xf in (0.0, 0.32, 0.64):
                left = int(width*xf)
                crop_w = min(width-left, max(1, int(width*0.36)))
                rect = (left, 0, left+crop_w, header_h)
                if crop_w > width*0.20 and header_h > height*0.06:
                    regions.append((*rect, 'header'))
            regions = list(dict.fromkeys(regions))
        with RAPID_LOCK:
            if RAPID_ENGINE is None:
                RAPID_ENGINE = RapidOCR()
            lines = []
            for left, top, right_edge, bottom_edge, scan in regions:
                crop = source.crop((left, top, right_edge, bottom_edge))
                scale = min(3.5, 2500/max(crop.size))
                if scale > 1:
                    crop = crop.resize((int(crop.width*scale), int(crop.height*scale)), Image.Resampling.LANCZOS)
                # Preserve the original pixels for OCR and also scan a locally
                # enhanced version of tiny title/price-label crops. Contrast and
                # a restrained unsharp mask can help mild blur; no pixels are
                # uploaded or written to disk. Full overview remains a single pass.
                variants = [crop]
                if scan != 'full':
                    gray = ImageOps.grayscale(crop)
                    enhanced = ImageOps.autocontrast(gray, cutoff=0.5)
                    enhanced = ImageEnhance.Contrast(enhanced).enhance(1.45)
                    enhanced = enhanced.filter(ImageFilter.UnsharpMask(radius=1.2, percent=150, threshold=3))
                    variants.append(enhanced.convert('RGB'))
                for variant in variants:
                    output = RAPID_ENGINE(np.asarray(variant))
                    for text, box, score in zip(output.txts if output.txts is not None else (),
                                               output.boxes if output.boxes is not None else (),
                                               output.scores if output.scores is not None else ()):
                        if not text or float(score) < 0.20:
                            continue
                        xs = [left+float(point[0])/scale for point in box]
                        ys = [top+float(point[1])/scale for point in box]
                        x, y = min(xs)/width, min(ys)/height
                        right, bottom = max(xs)/width, max(ys)/height
                        lines.append({'text': str(text), 'x': x, 'y': y, 'right': right,
                                      'bottom': bottom, 'words': [{'text': str(text), 'x': x, 'y': y,
                                      'width': max(0.001, right-x), 'height': max(0.001, bottom-y)}], 'scan': scan})
        if not lines:
            raise LocalAIError('RapidOCR found no readable chart text. Try a closer, clearer chart screenshot.')
        return {'lines': lines, 'width': width, 'height': height, 'engine': 'RapidOCR / ONNX Runtime (local)'}
    except LocalAIError:
        raise
    except Exception as error:
        raise LocalAIError('RapidOCR could not initialize locally: '+str(error)[:240]) from None

def status():
    global STATUS_CACHE, STATUS_TIME
    if STATUS_CACHE is not None and time.monotonic()-STATUS_TIME < 120:
        return STATUS_CACHE
    try:
        if os.name == 'nt':
            native_ocr()
        import rapidocr, onnxruntime
        result = {'connected': True, 'engine': 'RapidOCR '+onnxruntime.__version__+' / ONNX Runtime (local) + chart parser', 'independent': True, 'apiVersion': API_VERSION,
                  'message': 'RapidOCR is ready with enlarged, contrast-enhanced local passes for chart titles and price labels. Images are not sent to a cloud service.'}
    except LocalAIError as error:
        result = {'connected': False, 'engine': 'Built-in Windows OCR', 'independent': True, 'apiVersion': API_VERSION, 'message': str(error)}
    STATUS_CACHE, STATUS_TIME = result, time.monotonic()
    return result

def price(text):
    try:
        value = float(text.replace(',', '').replace(' ', ''))
        return round(value, 4) if math.isfinite(value) and value > 0 else None
    except (ValueError, TypeError):
        return None

def known_stock_symbols():
    """Reuse the application's user-facing NSE symbol suggestions for OCR fallback."""
    try:
        source = (ROOT/'app.js').read_text(encoding='utf-8')
        match = re.search(r'const\s+stockSymbols\s*=\s*`([^`]*)`', source)
        return sorted(set(re.findall(r'[A-Z0-9&._-]{2,30}', match.group(1).upper()))) if match else []
    except OSError:
        return []

def positioned_lines(document):
    lines = []
    for raw in document.get('lines', [])[:2500]:
        if not isinstance(raw, dict) or not isinstance(raw.get('text'), str):
            continue
        words = [w for w in raw.get('words', []) if isinstance(w, dict) and all(type(w.get(k)) in (int, float) and math.isfinite(w[k]) for k in ('x', 'y', 'width', 'height'))]
        if not words:
            continue
        x, y = min(w['x'] for w in words), min(w['y'] for w in words)
        right, bottom = max(w['x']+w['width'] for w in words), max(w['y']+w['height'] for w in words)
        lines.append({'text': raw['text'][:1500], 'x': x, 'y': y, 'right': right, 'bottom': bottom, 'words': words, 'scan': raw.get('scan')})
    return lines

def parse_chart(document):
    lines = positioned_lines(document)
    warnings, observations, notes, evidence, markers, ambiguous = [], [], [], {}, {}, []
    result = {'symbol': None, 'timeframe': None, 'timeframeLabel': None, 'setup': None, 'higherTimeframe': None,
              'entry': None, 'sl': None, 'target': None, 'targetSource': 'missing', 'axis': None}
    titles = []
    for line in lines:
        match = re.search(r'([A-Z0-9&._-]{1,30})\s*[\[({]\s*(?:NSE|BSE)\b([^\])}]*)[\])}]\s*([A-Z]+|\d+(?:\s*MIN)?)?', line['text'], re.I)
        if match:
            titles.append((match, line))
    # Cropped detail scans may truncate the stock title. A full-image title
    # takes precedence; detail scans primarily recover the marked prices.
    full_titles = [(m, line) for m, line in titles if line.get('scan') == 'full']
    if full_titles:
        titles = full_titles
    symbols = {m.group(1).upper() for m, _ in titles}
    if len(symbols) == 1:
        match, line = titles[0]
        result['symbol'] = match.group(1).upper()
        token = (match.group(3) or '').strip().upper()
        if not token:
            inside = match.group(2) or ''
            trailing = re.search(r'(?<!\d)(\d{1,4})(?:\s*MIN)?\s*$', inside, re.I)
            if trailing:
                token = trailing.group(1)
        if token in TIMEFRAMES:
            result['timeframe'], result['timeframeLabel'] = token, TIMEFRAMES[token]
        elif re.fullmatch(r'[1-9]\d{0,3}(?:\s*MIN)?', token):
            minutes = re.match(r'\d+', token).group()
            result['timeframe'] = minutes + ' min'
            result['timeframeLabel'] = result['timeframe']
        evidence['symbol'] = line['text']
        evidence['timeframe'] = line['text']
    elif len(symbols) > 1:
        warnings.append('More than one chart title was found. Paste or upload each chart as a separate image.')
    # Some chart windows OCR their compact header as separate fragments, or
    # omit the [NSE - id] wrapper. Recover the ticker only from the top header
    # using the app's suggestion universe, never from candle/annotation text.
    header_lines = [line for line in lines if line['y'] <= 0.10 and line['x'] < 0.99]
    close_header_lines = [line for line in header_lines if line.get('scan') == 'header']
    full_header_lines = [line for line in header_lines if line.get('scan') == 'full']
    header_tiers = [tier for tier in (close_header_lines, full_header_lines, header_lines) if tier]
    if not result['symbol'] and len(symbols) <= 1 and header_lines:
        known = known_stock_symbols()
        for tier in header_tiers:
            matches = set()
            for line in tier:
                text = line['text'].upper()
                for symbol in known:
                    if re.search(r'(?<![A-Z0-9])'+re.escape(symbol)+r'(?![A-Z0-9])', text):
                        matches.add(symbol)
            if len(matches) == 1:
                result['symbol'] = next(iter(matches))
                evidence['symbol'] = next(line['text'] for line in tier if result['symbol'] in line['text'].upper())
                break
            if len(matches) > 1:
                warnings.append('Several stock symbols appear in the chart header. Review the title before adding this plan.')
                break
    # Read execution interval from the title strip independently of the ticker.
    # OCR may return the title in fragments, and punctuation around the quote
    # or the trailing time can prevent an end-anchored match. Clean those known
    # title fields first, then accept only NSE chart intervals we support.
    if not result['timeframe'] and header_lines:
        # Analyze all header crops together. A left header crop may contain a
        # lot number such as 20 while a later crop contains the actual interval
        # at the far right (240); deciding per crop would select the wrong one.
        intervals = []
        for line in header_lines:
            text = line['text'].strip().upper()
            clean = re.sub(r'\b\d{1,2}:\d{2}(?::\d{2})?\b', ' ', text)
            clean = re.sub(r'\b\d{1,4}\s*@\s*[\d,]+(?:\.\d+)?', ' ', clean)
            clean = re.sub(r'@\s*[\d,]+(?:\.\d+)?', ' ', clean)
            clean = re.sub(r'\b(?:NSE|BSE)\s*[- ]?\s*\d+\b', ' ', clean)
            spans = []
            named = re.search(r'(?:^|[\s\])])([DWMQYH])(?:\s|$)', clean)
            if named:
                spans.append((named.start(1), named.end(1), named.group(1)))
            for numeric in re.finditer(r'(?<![A-Z0-9])([1-9]\d{0,3})(?:\.0+)?\s*(MIN(?:UTES?)?|M)?(?![A-Z0-9])', clean):
                minutes = int(numeric.group(1))
                if numeric.group(2) or minutes in NUMERIC_TIMEFRAMES:
                    spans.append((numeric.start(1), numeric.end(1), str(minutes)+' min'))
            for start, end, value in spans:
                center = ((start+end)/2)/max(1, len(clean))
                header_x = line['x']+(line['right']-line['x'])*center
                intervals.append((header_x, value, line))
        if intervals:
            rightmost_x = max(x for x, _, _ in intervals)
            rightmost = [item for item in intervals if abs(item[0]-rightmost_x) < 0.005]
            rightmost_values = {value for _, value, _ in rightmost}
            if len(rightmost_values) == 1:
                value = rightmost[0][1]
                result['timeframe'] = value
                result['timeframeLabel'] = TIMEFRAMES.get(value, value)
                evidence['timeframe'] = rightmost[0][2]['text']
            else:
                warnings.append('Several possible execution intervals appear at the right side of the chart header; review the timeframe before adding.')
    for field, label in LABELS.items():
        found = []
        candidates = [(line, line['text']) for line in lines]
        # Windows OCR can split the number and its colored marker into two
        # adjacent boxes on the same baseline. Rejoin only close, same-row
        # fragments so unrelated chart prices cannot become label values.
        for index, left in enumerate(lines):
            left_center = (left['y']+left['bottom'])/2
            for right in lines[index+1:]:
                right_center = (right['y']+right['bottom'])/2
                # OCR returns line fragments in scan order, not necessarily
                # left-to-right order. Measure the interval gap symmetrically;
                # treating a fragment to the left as zero-gap can join an
                # unrelated axis value to an Entry/SL/Target label.
                gap = max(0.0, max(left['x'], right['x'])-min(left['right'], right['right']))
                if abs(left_center-right_center) > 0.012 or gap > 0.10:
                    continue
                combined = left['text']+' '+right['text'] if left['x'] <= right['x'] else right['text']+' '+left['text']
                has_label = (re.search(r'\b'+label+r'\b', combined, re.I) or
                             re.search(r'\b'+REVERSED_LABELS[field]+r'\b', combined, re.I))
                has_entry_rr = field == 'entry' and re.search(r'\bRR(?:P)?\s*\(', combined, re.I)
                if not (has_label or has_entry_rr) or not re.search(NUMBER, combined, re.I):
                    continue
                candidates.append(({'x':min(left['x'],right['x']), 'y':min(left['y'],right['y']),
                                    'bottom':max(left['bottom'],right['bottom']), 'text':combined}, combined))
        for line, candidate_text in candidates:
            if not 0.10 <= line['y'] <= 0.90:
                continue
            match = re.search(r'\b'+label+r'\s*[:=.-]{0,2}\s*(?:Rs\.?\s*)?'+NUMBER, candidate_text, re.I)
            if not match:
                # Some charting templates place the price first and a short
                # marker after it, e.g. "7551.50 E" or "8184.40 T".
                match = re.search(r'\b'+NUMBER+r'\s*(?:Rs\.?\s*)?'+REVERSED_LABELS[field]+r'\b', candidate_text, re.I)
            if not match and field == 'entry':
                # TradeTiger entry annotations commonly print the entry price
                # followed by its explicit risk-reward marker: "6991.50 RR(2.00)".
                # OCR sometimes merges a neighboring line stroke into RR as RRP.
                match = re.search(r'\b'+NUMBER+r'\s+RR(?:P)?\s*\(\s*\d+(?:\.\d+)?\s*\)', candidate_text, re.I)
            if match and price(match.group(1)):
                origin = next((source for source in lines if source['x']==line['x'] and source['y']==line['y']), line)
                found.append((price(match.group(1)), origin, bool(re.search(r'\d[,.]\s*\d', match.group(1)))))
        # Enlarged overlapping crops are useful when the full chart missed a
        # tiny label, but a crop can begin halfway through a number and create
        # a plausible-looking false price (e.g. 2138.10 -> 39.10). Prefer a
        # full-chart label for each field whenever one was recognized.
        if any(line.get('scan') == 'full' for _, line, _ in found):
            found = [item for item in found if item[1].get('scan') == 'full']
        # Overlapping OCR crops can return multiple, conflicting readings of
        # the same drawn label. Pick the most complete text at each location;
        # different labels at different chart positions remain ambiguous.
        unique = []
        for value, line, has_decimal in found:
            center_y = (line['y']+line['bottom'])/2
            nearby = next((item for item in unique
                           if abs(item[1]['x']-line['x']) <= 0.025 and
                           abs((item[1]['y']+item[1]['bottom'])/2-center_y) <= 0.018), None)
            text = line['text']
            score = (100 if has_decimal else 0) + min(len(text), 120)/10 + (20 if re.search(r'\b(?:Rs\.?|RR)\b|\(', text, re.I) else 0)
            if nearby is None:
                unique.append([value, line, score])
            elif score > nearby[2]:
                nearby[:]=[value,line,score]
        found = [(value,line) for value,line,_ in unique]
        values = {value for value, _ in found}
        if len(values) == 1:
            result[field] = found[0][0]
            line = found[0][1]
            evidence[field] = line['text']
            markers[field] = {'x': max(0, min(1, line['x'])), 'y': max(0, min(1, line['bottom']+0.002))}
        elif len(values) > 1:
            ambiguous.append(field)
            warnings.append('Several different '+field+' prices were marked. Split the chart into one setup per image.')
    if result['target']:
        result['targetSource'] = 'marked on chart'
    # A chart can lose its decimal point during OCR (for example, 2213.30
    # becomes 221330.00). TradeTiger prints the target reward, stop risk and
    # entry R:R beside the lines. When those independent annotations agree,
    # use them to repair bad OCR prices instead of accepting a 100x value.
    if result['entry'] and result['sl'] and result['target']:
        rewards, risks, ratios = [], [], []
        # A full overview can miss tiny print that a detail crop reads. Use
        # evidence from every OCR scale; only unique reward/risk/RR readings
        # are trusted below, so overlapping crop copies do not weaken safety.
        reconciliation_lines = lines
        for line in reconciliation_lines:
            text = line['text']
            if re.search(r'\b(?:TGT|Target|T)\s*[:=.-]?', text, re.I):
                delta = re.search(r'\bRs\.?\s*\(?\s*(\d[\d,]*(?:\.\s*\d{1,4})?)', text, re.I)
                if delta and price(delta.group(1)):
                    rewards.append(price(delta.group(1)))
            if re.search(r'\b(?:Stop|SL)\b', text, re.I):
                delta = re.search(r'\bRs\.?\s*\(?\s*-\s*(\d[\d,]*(?:\.\s*\d{1,4})?)', text, re.I)
                if delta and price(delta.group(1)):
                    risks.append(price(delta.group(1)))
            ratio = re.search(r'\bRR(?:P|C)?\s*\(\s*(\d+(?:\.\d+)?)', text, re.I)
            if ratio:
                ratios.append(float(ratio.group(1)))
        if len(set(rewards)) == 1 and len(set(risks)) == 1 and len(set(ratios)) == 1:
            reward, risk, ratio = rewards[0], risks[0], ratios[0]
            if risk > 0 and abs(reward/risk-ratio) <= max(0.08, ratio*0.05):
                expected_target = round(result['entry']+reward, 4)
                expected_sl = round(result['entry']-risk, 4)
                if expected_sl > 0 and expected_sl < result['entry'] < expected_target:
                    repaired = []
                    if abs(result['sl']-expected_sl) > max(0.05, risk*0.03):
                        result['sl'] = expected_sl
                        evidence['sl'] = 'Corrected from the printed Stop Rs risk and Entry after confirming the printed R:R.'
                        repaired.append('stop-loss')
                    if abs(result['target']-expected_target) > max(0.05, reward*0.03):
                        result['target'] = expected_target
                        result['targetSource'] = 'reconstructed from printed reward and verified R:R'
                        evidence['target'] = 'Corrected from the printed Target Rs reward and Entry after confirming the printed R:R.'
                        repaired.append('target')
                    if repaired:
                        observations.append('OCR price reading was corrected for '+ ' and '.join(repaired) +' using the chart’s printed Rs distances and R:R. Verify against the original chart before adding.')
    # TradeTiger labels print both reward and risk beside their prices
    # (for example, T:2263.50 Rs(248.10), Stop:1911.40 Rs(-104.00)).
    # If horizontal strokes obscure a leading digit, recover only when the
    # two explicit distances and the printed RR independently agree.
    if result['target'] and (result['entry'] is None or result['sl'] is None or
                             (result['entry'] is not None and result['sl'] is not None and
                              result['entry']-result['sl'] < 1)):
        rewards, risks, ratios = [], [], []
        for line in lines:
            text = line['text']
            if re.search(r'\b(?:TGT|Target|T)\s*[:=.-]?', text, re.I):
                delta = re.search(r'\bRs\.?\s*\(?\s*(\d[\d,]*(?:\.\s*\d{1,4})?)', text, re.I)
                if delta: rewards.append(price(delta.group(1)))
            if re.search(r'\b(?:Stop|SL)\b', text, re.I):
                delta = re.search(r'\bRs\.?\s*\(?\s*-\s*(\d[\d,]*(?:\.\s*\d{1,4})?)', text, re.I)
                if delta: risks.append(price(delta.group(1)))
            ratio = re.search(r'\bRR(?:P|C)?\s*\(\s*(\d+(?:\.\d+)?)', text, re.I)
            if ratio: ratios.append(float(ratio.group(1)))
        pairs = [(reward, risk) for reward in rewards for risk in risks
                 if reward and risk and abs(reward/risk-(ratios[0] if ratios else 0)) <= 0.08]
        if len(pairs) == 1:
            reward, risk = pairs[0]
            recovered_entry = round(result['target']-reward, 4)
            recovered_sl = round(recovered_entry-risk, 4)
            if recovered_sl > 0 and recovered_sl < recovered_entry < result['target']:
                result['entry'], result['sl'] = recovered_entry, recovered_sl
                evidence['entry'] = 'Recovered from marked target and explicit Rs reward; verified against printed RR.'
                evidence['sl'] = 'Recovered from explicit Rs risk annotation and verified entry.'
                observations.append('Entry and stop were reconstructed from the chart’s printed reward, risk and R:R annotations; review both values before adding.')
        elif result['entry'] and result['sl'] is None and len(set(rewards)) == 1 and len(set(ratios)) == 1:
            reward, ratio = rewards[0], ratios[0]
            if reward and ratio > 0:
                recovered_sl = round(result['entry']-reward/ratio, 4)
                if 0 < recovered_sl < result['entry'] < result['target'] and abs((result['target']-result['entry'])/reward-1) <= 0.03:
                    result['sl'] = recovered_sl
                    evidence['sl'] = 'Calculated from marked Entry, Target reward amount and printed R:R; review before adding.'
                    observations.append('Stop-loss was reconstructed from the chart’s explicit target reward and printed R:R; verify it against the original stop marking.')
    # Setup comes from the higher-timeframe zone, never from the chart execution timeframe.
    explicit = [(code, line) for line in lines if 0.1 <= line['y'] <= 0.9 for code in re.findall(r'\b(HYIT|YIT|QIT|MIT|WIT|DIT)\b', line['text'], re.I)]
    zones = []
    for line in lines:
        if not 0.1 <= line['y'] <= 0.9:
            continue
        for match in re.finditer(r'\b(RB[0-9IL]*|DB[0-9IL]*|RBR|DBR|RBD|DBD|HTF|HIGHER\s*TIMEFRAME|H[1-9])\s*[-:]?\s*(HY|H|Y|Q|M|W|D)\b', line['text'], re.I):
            distance = abs(line['y']-markers.get('entry', {}).get('y', line['y']))
            zones.append((distance, match.group(2).upper(), line))
    if len({code.upper() for code, _ in explicit}) == 1:
        result['setup'] = explicit[0][0].upper()
        token = next(key for key, value in SETUPS.items() if value == result['setup'])
        result['higherTimeframe'] = HIGHER_TIMEFRAMES.get(token)
        evidence['setup'] = explicit[0][1]['text']
    elif len({code.upper() for code, _ in explicit}) > 1:
        warnings.append('Different setup labels were found. Review a crop containing only the intended setup.')
    elif zones:
        zones.sort(key=lambda item: item[0])
        close = [z for z in zones if z[0] <= zones[0][0]+0.015]
        if len({z[1] for z in close}) == 1:
            token, line = zones[0][1:]
            result['setup'] = SETUPS[token]
            result['higherTimeframe'] = HIGHER_TIMEFRAMES.get(token)
            evidence['setup'] = line['text']
            observations.append(line['text']+' identifies the '+result['higherTimeframe']+' zone, mapped to '+result['setup']+'.')
        else:
            warnings.append('Conflicting higher-timeframe zones are equally close to the entry label; use a clearer crop.')
    for line in lines:
        text = line['text'].strip()
        if not (0.12 < line['y'] < 0.87 and line['x'] < 0.90) or text in evidence.values():
            continue
        if re.search(r'\b(?:CANDLESTICK|AVG\s*\(|EMA|SMA|SHAREKHAN|TRADETIGER|LIVE\s*CHAT|BID|ASK|NSE\s*ONLINE)\b', text, re.I):
            continue
        if re.search(r'\b(?:RB[0-9IL]*|DB[0-9IL]*|H\d*|HTF)\s*[-:]\s*(?:HY|H|Y|Q|M|W|D)\b', text, re.I):
            continue
        if re.search(r'\b\d{1,2}[-/](?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[-/]\d{2,4}\b', text, re.I):
            continue
        # OCR often clips the first character of Entry or merges a line stroke
        # into Stop/Target (for example, "ntry:1004.55 RR" or
        # "Stopt869.40 Rs"). Keep these as price evidence, never as trade notes.
        has_price_digits = bool(re.search(r'\d', text))
        annotation_marker = re.search(
            r'(?:\b(?:entry|entr[yv]|ntry|stop(?:loss)?t?|s\.?l\.?|target|tgt|top)\b|'
            r'\bT\s*[:=.-]|\bRR(?:P|C)?\b|\bRs\.?\s*[(-])', text, re.I)
        if has_price_digits and annotation_marker:
            continue
        if any(re.search(r'\b'+label+r'\s*[:=\-]?\s*(?:Rs\.?\s*)?'+NUMBER, text, re.I) or re.search(r'\b'+NUMBER+r'\s*(?:Rs\.?\s*)?'+REVERSED_LABELS[field]+r'\b', text, re.I) for field,label in LABELS.items()):
            continue
        if len(re.findall(r'[A-Za-z]{2,}', text)) >= 2 and not re.fullmatch(r'[\d.,: /+%-]+', text) and text not in notes:
            notes.append(text[:500])
            if len(notes)>=20: break
    axis = []
    for line in lines:
        if line['x'] > 0.91 and 0.07 < line['y'] < 0.91 and re.fullmatch(r'\d[\d,]*\.\d{1,2}', line['text'].strip()):
            value = price(line['text'].strip())
            if value is not None:
                axis.append(((line['y']+line['bottom'])/2, value))
    if len(axis) >= 3:
        axis.sort()
        y1, p1 = axis[0]; y2, p2 = axis[-1]
        if y2 > y1 and p1 > p2:
            slope = (p2-p1)/(y2-y1); intercept = p1-slope*y1
            errors = sorted(abs(p-(intercept+slope*y)) for y, p in axis)
            if errors[len(errors)//2] < (p1-p2)*0.03:
                result['axis'] = {'intercept': intercept, 'slope': slope}
    required = ['symbol', 'timeframe', 'setup', 'entry', 'sl']
    missing = [field for field in required if not result[field]]
    if missing:
        warnings.append('Not read: '+', '.join(missing)+'. Use a clearer screenshot including the chart title, zone label and marked prices.')
    if result['target'] is None:
        warnings.append('No target label was read. It has not been guessed. You can explicitly use the configured R:R target during review.')
    has_entry_sl = all(isinstance(result[field], (int, float)) and math.isfinite(result[field]) and result[field] > 0 for field in ('entry', 'sl'))
    has_target = isinstance(result['target'], (int, float)) and math.isfinite(result['target']) and result['target'] > 0
    if has_entry_sl and result['sl'] >= result['entry']:
        warnings.append('Marked SL is not below entry; this is not a valid delivery-buy setup.')
    if has_target and result['entry'] and result['target'] <= result['entry']:
        warnings.append('Marked target is not above entry; review the chart.')
    result.update(notes=notes, evidence=evidence, markers=markers, warnings=warnings, observations=observations,
                  usable=not missing and has_entry_sl and has_target and result['sl'] < result['entry'] and result['target'] > result['entry'],
                  canCalculateTarget=not missing and has_entry_sl and result['sl'] < result['entry'] and result['target'] is None and 'target' not in ambiguous,
                  rawText='\n'.join(line['text'] for line in lines)[:30000])
    return result

def analyze(body):
    if not isinstance(body, dict) or not isinstance(body.get('image'), str) or len(body['image']) > 7000000:
        raise LocalAIError('Use a PNG or JPEG screenshot under 5 MB.')
    try:
        raw = base64.b64decode(body['image'], validate=True)
    except (ValueError, binascii.Error):
        raise LocalAIError('Invalid image data.') from None
    if not 0 < len(raw) <= 5000000 or not (raw.startswith(b'\x89PNG\r\n\x1a\n') or raw.startswith(b'\xff\xd8\xff')):
        raise LocalAIError('Use a PNG or JPEG screenshot under 5 MB.')
    if not BUSY.acquire(blocking=False):
        raise LocalAIError('The built-in reader is busy with another chart. Retry shortly.')
    try:
        document = rapid_ocr(body['image'])
        return {'engine': document.get('engine'), 'apiVersion': API_VERSION,
                'analysis': parse_chart(document), 'localOnly': True}
    finally:
        BUSY.release()
