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
SETUPS = {'Y': 'YIT', 'H': 'HYIT', 'HY': 'HYIT', 'Q': 'QIT', 'M': 'MIT', 'W': 'WIT', 'D': 'DIT'}
HIGHER_TIMEFRAMES = {'Y': 'Yearly', 'H': 'Half-yearly', 'HY': 'Half-yearly', 'Q': 'Quarterly', 'M': 'Monthly', 'W': 'Weekly', 'D': 'Daily'}
TIMEFRAMES = {'Y': 'Yearly', 'Q': 'Quarterly', 'M': 'Monthly', 'W': 'Weekly', 'D': 'Daily', 'H': 'Hourly'}
NUMBER = r'(\d[\d,]*(?:\.\s*\d{1,4})?)(?![\w.,])'
LABELS = {'entry': r'(?:Entr[yv]|E(?=\s*[:=.-]))', 'sl': r'(?:Sto[po](?:[ -]?loss)?|S\.?L\.?)', 'target': r'(?:Target|TGT|T(?=\s*[:=.-]))'}
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

def status():
    global STATUS_CACHE, STATUS_TIME
    if STATUS_CACHE is not None and time.monotonic()-STATUS_TIME < 120:
        return STATUS_CACHE
    try:
        native_ocr()
        result = {'connected': True, 'engine': 'Built-in Windows OCR + chart label parser', 'independent': True, 'apiVersion': 2,
                  'message': 'Built-in offline chart reader ready. No Ollama, cloud API or separate AI application is needed.'}
    except LocalAIError as error:
        result = {'connected': False, 'engine': 'Built-in Windows OCR', 'independent': True, 'apiVersion': 2, 'message': str(error)}
    STATUS_CACHE, STATUS_TIME = result, time.monotonic()
    return result

def price(text):
    try:
        value = float(text.replace(',', '').replace(' ', ''))
        return round(value, 4) if math.isfinite(value) and value > 0 else None
    except (ValueError, TypeError):
        return None

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
        match = re.search(r'([A-Z0-9&._-]{1,30})\s*[\[({]\s*(?:NSE|BSE)\b[^\])}]*[\])}]\s*([A-Z]+|\d+(?:\s*MIN)?)?', line['text'], re.I)
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
        token = (match.group(2) or '').strip().upper()
        if token in TIMEFRAMES:
            result['timeframe'], result['timeframeLabel'] = token, TIMEFRAMES[token]
        elif re.fullmatch(r'[1-9]\d{0,3}(?:\s*MIN)?', token):
            result['timeframe'] = re.match(r'\d+', token).group() + ' min'
            result['timeframeLabel'] = result['timeframe']
        evidence['symbol'] = line['text']
        evidence['timeframe'] = line['text']
    elif len(symbols) > 1:
        warnings.append('More than one chart title was found. Paste or upload each chart as a separate image.')
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
                gap = max(0.0, right['x']-left['right'])
                if abs(left_center-right_center) > 0.012 or gap > 0.10:
                    continue
                combined = left['text']+' '+right['text'] if left['x'] <= right['x'] else right['text']+' '+left['text']
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
        return {'engine': 'Built-in Windows OCR + chart label parser', 'analysis': parse_chart(native_ocr(body['image'])), 'localOnly': True}
    finally:
        BUSY.release()
