import base64
import json
import os
import subprocess
import unittest
from unittest.mock import patch
import chart_ai

try:
    import rapidocr  # noqa: F401
    RAPID_OCR_AVAILABLE=True
except ImportError:
    RAPID_OCR_AVAILABLE=False

def line(text, y, x=0.2):
    return {'text': text, 'words': [{'text': text, 'x': x, 'y': y, 'width': 0.2, 'height': 0.015}]}

def cyient(target=False):
    lines = [line('CYIENT[NSE - 5748] D', 0.005, 0.005), line('This is followup zone', 0.32, 0.42), line('H1-W :985.45',0.53,0.01),
             line('Entry: 914.20',0.62,0.65), line('Stop:879.10 Rs(-35.10)',0.67,0.65), line('RB3-M',0.64,0.32)]
    if target: lines.append(line('T: 1000.00',0.5,0.65))
    return {'lines':lines}

class ChartReaderTests(unittest.TestCase):
    def test_cyient_monthly_setup_daily_execution_and_chart_note(self):
        result=chart_ai.parse_chart(cyient())
        self.assertEqual(result['symbol'],'CYIENT')
        self.assertEqual(result['entry'],914.2)
        self.assertEqual(result['sl'],879.1)
        self.assertEqual(result['setup'],'MIT')
        self.assertEqual(result['timeframe'],'D')
        self.assertEqual(result['higherTimeframe'],'Monthly')
        self.assertIn('This is followup zone',result['notes'])
        self.assertIsNone(result['target'])
        self.assertFalse(result['usable'])
        self.assertTrue(result['canCalculateTarget'])

    def test_target_is_read_only_from_marked_price_not_reference_level(self):
        result=chart_ai.parse_chart(cyient(True))
        self.assertEqual(result['target'],1000)
        self.assertTrue(result['usable'])
        self.assertNotIn('H1-W :985.45',result['notes'])

    def test_bayercrop_half_yearly_zone_with_weekly_execution(self):
        document={'lines':[line('BAYERCROP[NSE - 17927] W',0.005,0.005),
                           line('RB1-H :TE:3563.48',0.69),line('RB1-H :BE:2927.77',0.76),
                           line('Entry: 3339.10 RR(2.08)',0.71),line('Stop:2848.30 Rs(-490.8)',0.78),
                           line('T:4357.70 Rs(1018.6)',0.58)]}
        result=chart_ai.parse_chart(document)
        self.assertEqual(result['setup'],'HYIT')
        self.assertEqual(result['higherTimeframe'],'Half-yearly')
        self.assertEqual(result['timeframe'],'W')
        self.assertEqual(result['entry'],3339.1)
        self.assertEqual(result['sl'],2848.3)
        self.assertEqual(result['target'],4357.7)
        self.assertTrue(result['usable'])
        document['lines'][1]['text']='RBI-H :TE:3563.48'
        self.assertEqual(chart_ai.parse_chart(document)['setup'],'HYIT')

    def test_whirlpool_weekly_h1_h2_zones_and_marked_prices(self):
        document={'lines':[line('WHIRLPOOL[NSE - 18011] D',0.005,0.005),
                           line('H1-W : 724.66',0.81,0.005),line('H2-W : 705.88',0.85,0.005),
                           line('Entry: 731.45 RR(2.03)',0.80,0.60),
                           line('Stop: 698.80 Rs(-32.65)',0.87,0.60),
                           line('T: 797.65 Rs(66.20)',0.66,0.73)]}
        result=chart_ai.parse_chart(document)
        self.assertEqual(result['symbol'],'WHIRLPOOL')
        self.assertEqual(result['timeframe'],'D')
        self.assertEqual(result['setup'],'WIT')
        self.assertEqual(result['higherTimeframe'],'Weekly')
        self.assertEqual(result['entry'],731.45)
        self.assertEqual(result['sl'],698.8)
        self.assertEqual(result['target'],797.65)
        self.assertTrue(result['usable'],result['warnings'])
        self.assertNotIn('H1-W : 724.66',result['notes'])

    def test_polycab_chart_template_places_price_before_entry_stop_target_marker(self):
        document={'lines':[line('POLYCAB[NSE - 9500] W',0.005,0.005),
                           line('H1 - Q : 7626.93',0.62,0.01),
                           line('H2 - Q : 7066.93',0.78,0.01),
                           line('7551.50 E RR(2.00)',0.73,0.42),
                           line('6939.60 Stop Rs(-612.90)',0.84,0.47),
                           line('8184.40 T Rs(632.90)',0.56,0.72)]}
        result=chart_ai.parse_chart(document)
        self.assertEqual(result['symbol'],'POLYCAB')
        self.assertEqual(result['entry'],7551.5)
        self.assertEqual(result['sl'],6939.6)
        self.assertEqual(result['target'],8184.4)
        self.assertEqual(result['setup'],'QIT')
        self.assertTrue(result['usable'],result['warnings'])

    def test_fragmented_ocr_boxes_rejoin_short_markers_with_prices(self):
        def box(text,y,x,width):
            return {'text':text,'words':[{'text':text,'x':x,'y':y,'width':width,'height':0.012}]}
        document={'lines':[line('TESTCO[NSE - 9500] W',0.005,0.005),
                           line('H1-Q :105.00',0.62,0.01),
                           box('E:',0.70,0.40,0.025),box('100.00 RR(2.00)',0.70,0.43,0.12),
                           box('Stop:',0.78,0.40,0.04),box('90.00 Rs(-10.00)',0.78,0.445,0.13),
                           box('T:',0.56,0.72,0.025),box('120.00 Rs(20.00)',0.56,0.75,0.14)]}
        result=chart_ai.parse_chart(document)
        self.assertEqual(result['entry'],100)
        self.assertEqual(result['sl'],90)
        self.assertEqual(result['target'],120)
        self.assertTrue(result['usable'],result['warnings'])

    def test_price_far_left_of_label_is_not_joined_as_entry(self):
        document={'lines':[line('TESTCO[NSE - 9500] W',0.005,0.005),
                           line('H1-Q :105.00',0.62,0.01),
                           line('E:',0.70,0.65),line('200.00',0.70,0.10),
                           line('Stop:90.00',0.78,0.65),line('T:120.00',0.56,0.72)]}
        result=chart_ai.parse_chart(document)
        self.assertIsNone(result['entry'])
        self.assertFalse(result['usable'])

    def test_bdl_ocr_stop_artifact_is_read_and_price_annotations_are_not_notes(self):
        document={'lines':[line('BDL[NSE - 2144] W',0.005,0.005),
                           line('RB1-Q 0.41',0.62,0.01),
                           line('ntry: 1004.55 RR(2.14)',0.70,0.60),
                           line('Stopt869.40 Rs(-135.15)',0.78,0.60),
                           line('•top:ooy.4u',0.56,0.72),
                           line('This is followup zone',0.32,0.42)]}
        result=chart_ai.parse_chart(document)
        self.assertEqual(result['symbol'],'BDL')
        self.assertEqual(result['entry'],1004.55)
        self.assertEqual(result['sl'],869.40)
        self.assertIsNone(result['target'])
        self.assertIn('This is followup zone',result['notes'])
        self.assertFalse(any('1004.55' in note or '869.40' in note or 'ooy.4u' in note for note in result['notes']))
        self.assertFalse(result['usable'])

    def test_caplipoint_chart_entry_stop_target_and_daily_timeframe(self):
        document={'lines':[line('CAPLIPOINT[NSE - 3906] D',0.005,0.005),
                           line('H1-W :2062.92',0.64,0.01),
                           line('H2-W :1942.74',0.72,0.01),
                           line('Entry: 2015.40 RR(2.39)',0.68,0.14),
                           line('Stop:1911.40 Rs(-104)',0.75,0.14),
                           line('T:2263.50 Rs(248.1)',0.55,0.74)]}
        result=chart_ai.parse_chart(document)
        self.assertEqual(result['symbol'],'CAPLIPOINT')
        self.assertEqual(result['timeframe'],'D')
        self.assertEqual(result['setup'],'WIT')
        self.assertEqual(result['entry'],2015.40)
        self.assertEqual(result['sl'],1911.40)
        self.assertEqual(result['target'],2263.50)
        self.assertTrue(result['usable'],result['warnings'])

    def test_caplipoint_recovers_entry_and_stop_from_explicit_rr_deltas(self):
        document={'lines':[line('CAPLIPOINT[NSE - 3906] D',0.005,0.005),
                           line('H1-W :2062.92',0.64,0.01),
                           line('H2-W :1942.74',0.72,0.01),
                           line('RR(2.39)',0.68,0.14),
                           line('Stop. 11.40 Rs(-104)',0.75,0.14),
                           line('T:2263.50 Rs(248.1)',0.55,0.74)]}
        result=chart_ai.parse_chart(document)
        self.assertEqual(result['entry'],2015.4)
        self.assertEqual(result['sl'],1911.4)
        self.assertEqual(result['target'],2263.5)
        self.assertTrue(result['usable'],result['warnings'])

    def test_numeric_execution_interval_and_ocr_decimal_repair_from_chart_risk_reward(self):
        document={'lines':[line('CAPLIPOINT[NSE - 3906] 125',0.005,0.005),
                           line('H1-W :2062.92',0.64,0.01),
                           line('Entry: 2138.10 RR(2.53)',0.68,0.14),
                           line('Stop: 210840.00 Rs(-29.70)',0.75,0.14),
                           line('T: 221330.00 Rs(75.20)',0.55,0.74)]}
        result=chart_ai.parse_chart(document)
        self.assertEqual(result['symbol'],'CAPLIPOINT')
        self.assertEqual(result['timeframe'],'125 min')
        self.assertEqual(result['entry'],2138.10)
        self.assertEqual(result['sl'],2108.40)
        self.assertEqual(result['target'],2213.30)
        self.assertTrue(result['usable'],result['warnings'])
        self.assertIn('target',result['observations'][0])

    def test_decimal_repair_uses_risk_annotation_from_detail_crop(self):
        full=[line('CAPLIPOINT[NSE - 3906] 125',0.005,0.005),
              line('Entry: 2138.10',0.68,0.14),
              line('Stop: 210840.00',0.75,0.14),
              line('T: 221330.00',0.55,0.74)]
        detail=[line('Entry: 2138.10 RR(2.53)',0.68,0.14),
                line('Stop: 210840.00 Rs(-29.70)',0.75,0.14),
                line('T: 221330.00 Rs(75.20)',0.55,0.74)]
        for item in full: item['scan']='full'
        for item in detail: item['scan']='detail'
        result=chart_ai.parse_chart({'lines':full+detail})
        self.assertEqual(result['sl'],2108.40)
        self.assertEqual(result['target'],2213.30)
        self.assertTrue(any('stop-loss' in item for item in result['observations']))

    def test_compact_split_chart_header_recovers_symbol_and_execution_interval(self):
        document={'lines':[line('CAPLIPOINT',0.005,0.005),
                           line('NSE1 @ 2407.50 15:59:21 125',0.005,0.30),
                           line('H1-Y :2163.31',0.64,0.01),
                           line('Entry: 2138.10 RR(2.53)',0.68,0.14),
                           line('Stop:2108.40 Rs(-29.70)',0.75,0.14),
                           line('T:2213.30 Rs(75.20)',0.55,0.74)]}
        result=chart_ai.parse_chart(document)
        self.assertEqual(result['symbol'],'CAPLIPOINT')
        self.assertEqual(result['timeframe'],'125 min')
        self.assertEqual(result['setup'],'YIT')
        self.assertEqual(result['entry'],2138.10)
        self.assertEqual(result['sl'],2108.40)
        self.assertEqual(result['target'],2213.30)
        self.assertTrue(result['usable'],result['warnings'])

    @unittest.skipUnless(os.name=='nt' and RAPID_OCR_AVAILABLE,'Windows with local RapidOCR')
    def test_trade_tiger_title_with_exchange_quote_clock_and_125_interval(self):
        document={'lines':[line('CAPLIPOINT',0.005,0.005),
                           line('NSEITS 1 @ 2407.50 15:59:21 125 min',0.005,0.30),
                           line('H1-W :2062.92',0.64,0.01),
                           line('Entry: 2015.40 RR(2.39)',0.68,0.14),
                           line('Stop:1911.40 Rs(-104)',0.75,0.14),
                           line('T:2263.50 Rs(248.1)',0.55,0.74)]}
        result=chart_ai.parse_chart(document)
        self.assertEqual(result['timeframe'],'125 min',result['rawText'])
        self.assertEqual(result['timeframeLabel'],'125 min')
        self.assertTrue(result['usable'],result['warnings'])

    def test_small_compact_chart_title_is_found_by_enlarged_header_scan(self):
        from PIL import Image, ImageDraw, ImageFont
        import io
        image=Image.new('RGB',(1920,1080),'aliceblue');draw=ImageDraw.Draw(image)
        small=ImageFont.truetype('arial.ttf',9)
        draw.text((4,2),'CAPLIPOINT NSE1 @2407.50 15:59:21 125',font=small,fill='black')
        draw.text((580,500),'H1-Y :2163.31',font=small,fill='black')
        draw.text((800,650),'Entry: 2138.10 RR(2.53)',font=small,fill='black')
        draw.text((800,700),'Stop:2108.40 Rs(-29.70)',font=small,fill='red')
        draw.text((1450,500),'T:2213.30 Rs(75.20)',font=small,fill='green')
        stream=io.BytesIO();image.save(stream,format='PNG')
        document=chart_ai.rapid_ocr(base64.b64encode(stream.getvalue()).decode())
        result=chart_ai.parse_chart(document)
        self.assertEqual(result['symbol'],'CAPLIPOINT',result['rawText'])
        self.assertEqual(result['timeframe'],'125 min',result['rawText'])
        self.assertTrue(result['usable'],(result['warnings'],result['rawText']))

    def test_trade_tiger_rr_annotation_identifies_entry_price(self):
        document={'lines':[line('POLYCAB[NSE - 9500] W',0.005,0.005),
                           line('H1-Q :7626.93',0.62,0.01),
                           line('6991.50 RRP(2.00)',0.70,0.42),
                           line('Stop:6415.05 Rs(-576.45)',0.82,0.45),
                           line('T:8144.40 Rs(1152.90)',0.56,0.72)]}
        result=chart_ai.parse_chart(document)
        self.assertEqual(result['entry'],6991.5)
        self.assertEqual(result['sl'],6415.05)
        self.assertEqual(result['target'],8144.4)
        self.assertEqual(result['setup'],'QIT')
        self.assertTrue(result['usable'],result['warnings'])

    def test_invalid_zero_price_axis_ocr_does_not_crash_scale_calculation(self):
        document=cyient(True)
        document['lines'] += [line('0.00',0.25,0.95),line('900.00',0.40,0.95),line('800.00',0.55,0.95)]
        result=chart_ai.parse_chart(document)
        self.assertEqual(result['entry'],914.2)
        self.assertEqual(result['sl'],879.1)
        self.assertTrue(result['usable'])

    def test_missing_price_labels_are_reported_instead_of_comparing_none(self):
        document={'lines':[line('WHIRLPOOL[NSE - 18011] D',0.005,0.005),line('H1-W : 724.66',0.81,0.005)]}
        result=chart_ai.parse_chart(document)
        self.assertFalse(result['usable'])
        self.assertIn('entry',result['warnings'][0].lower())

    def test_conflicting_prices_and_multiple_charts_cannot_be_saved(self):
        document=cyient(True);document['lines'].append(line('Entry:950.00',0.7))
        self.assertFalse(chart_ai.parse_chart(document)['usable'])
        document=cyient(True);document['lines'].append(line('BDL[NSE - 1624] W',0.4))
        self.assertFalse(chart_ai.parse_chart(document)['usable'])

    def test_h1_weekly_zone_is_recognized_when_no_nearer_zone_exists(self):
        document=cyient(True);document['lines']=[l for l in document['lines'] if 'RB3' not in l['text']]
        result=chart_ai.parse_chart(document)
        self.assertEqual(result['setup'],'WIT');self.assertTrue(result['usable'])

    def test_cropped_title_cannot_override_full_chart_title(self):
        document=cyient(True)
        document['lines'][0]['scan']='full'
        cropped=line('YIENT[NSE - 5748] D',0.005,0.4)
        cropped['scan']='detail';document['lines'].append(cropped)
        self.assertEqual(chart_ai.parse_chart(document)['symbol'],'CYIENT')

    def test_invalid_image_never_runs_ocr(self):
        with patch('chart_ai.native_ocr') as read:
            for image in ['bad-base64',base64.b64encode(b'<svg/>').decode()]:
                with self.assertRaises(chart_ai.LocalAIError):chart_ai.analyze({'image':image})
            read.assert_not_called()

    def test_analysis_has_no_network_or_model_dependency(self):
        with patch('chart_ai.rapid_ocr',return_value=cyient(True)) as read:
            result=chart_ai.analyze({'image':base64.b64encode(b'\x89PNG\r\n\x1a\nfixture').decode()})
        self.assertTrue(result['localOnly']);self.assertTrue(result['analysis']['usable']);read.assert_called_once()

    @unittest.skipUnless(os.name=='nt','Windows native OCR')
    def test_small_price_labels_beside_horizontal_chart_lines(self):
        script = r'''Add-Type -AssemblyName System.Drawing
$bmp=[System.Drawing.Bitmap]::new(1920,920)
$g=[System.Drawing.Graphics]::FromImage($bmp)
$g.Clear([System.Drawing.Color]::AliceBlue)
$font=[System.Drawing.Font]::new('Arial',12,[System.Drawing.FontStyle]::Regular,[System.Drawing.GraphicsUnit]::Pixel)
$g.FillRectangle([System.Drawing.Brushes]::LightSteelBlue,730,622,1125,78)
$titleFont=[System.Drawing.Font]::new('Arial',18,[System.Drawing.FontStyle]::Regular,[System.Drawing.GraphicsUnit]::Pixel)
$g.DrawString('BAYERCROP[NSE - 17927] W',$titleFont,[System.Drawing.Brushes]::Black,4,4)
$titleFont.Dispose()
$g.DrawString('RB1-H :TE:3563.48',$font,[System.Drawing.Brushes]::Black,580,624)
$g.DrawString('Entry: 3339.10 RR(2.08)',$font,[System.Drawing.Brushes]::Black,760,638)
$g.DrawLine([System.Drawing.Pens]::Black,756,650,1855,650)
$g.DrawString('Stop:2848.30 Rs(-490.8)',$font,[System.Drawing.Brushes]::Red,760,697)
$g.DrawLine([System.Drawing.Pens]::Red,756,709,1855,709)
$g.DrawString('T:4357.70 Rs(1018.6)',$font,[System.Drawing.Brushes]::LimeGreen,1460,513)
$g.DrawLine([System.Drawing.Pens]::LimeGreen,1460,525,1855,525)
$memory=[System.IO.MemoryStream]::new()
$bmp.Save($memory,[System.Drawing.Imaging.ImageFormat]::Png)
[Convert]::ToBase64String($memory.ToArray())
$g.Dispose();$font.Dispose();$bmp.Dispose();$memory.Dispose()'''
        fixture=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',script],capture_output=True,text=True,timeout=20,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        self.assertEqual(fixture.returncode,0)
        result=chart_ai.analyze({'image':fixture.stdout.strip()})['analysis']
        self.assertEqual(result['entry'],3339.1,result['rawText'])
        self.assertEqual(result['sl'],2848.3,result['rawText'])
        self.assertEqual(result['target'],4357.7,result['rawText'])
        self.assertTrue(result['usable'],(result['warnings'],result['rawText']))

    @unittest.skipUnless(os.name=='nt','Windows native OCR')
    def test_real_windows_reader_whirlpool_weekly_zones_and_prices(self):
        script = r'''Add-Type -AssemblyName System.Drawing
$bmp=[System.Drawing.Bitmap]::new(1920,1032)
$g=[System.Drawing.Graphics]::FromImage($bmp)
$g.Clear([System.Drawing.Color]::AliceBlue)
$font=[System.Drawing.Font]::new('Arial',12,[System.Drawing.FontStyle]::Regular,[System.Drawing.GraphicsUnit]::Pixel)
$titleFont=[System.Drawing.Font]::new('Arial',18,[System.Drawing.FontStyle]::Regular,[System.Drawing.GraphicsUnit]::Pixel)
$g.DrawString('WHIRLPOOL[NSE - 18011] D',$titleFont,[System.Drawing.Brushes]::Black,3,3)
$g.DrawString('H1-W : 724.66',$font,[System.Drawing.Brushes]::Black,12,826)
$g.DrawString('H2-W : 705.88',$font,[System.Drawing.Brushes]::Black,12,872)
$g.DrawString('Entry: 731.45 RR(2.03)',$font,[System.Drawing.Brushes]::Black,1150,805)
$g.DrawLine([System.Drawing.Pens]::Black,1140,822,1855,822)
$g.DrawString('Stop: 698.80 Rs(-32.65)',$font,[System.Drawing.Brushes]::Red,1140,885)
$g.DrawLine([System.Drawing.Pens]::Red,1130,902,1855,902)
$g.DrawString('T:797.65 Rs(66.20)',$font,[System.Drawing.Brushes]::LimeGreen,1370,671)
$g.DrawLine([System.Drawing.Pens]::LimeGreen,1370,688,1855,688)
$memory=[System.IO.MemoryStream]::new()
$bmp.Save($memory,[System.Drawing.Imaging.ImageFormat]::Png)
[Convert]::ToBase64String($memory.ToArray())
$g.Dispose();$font.Dispose();$titleFont.Dispose();$bmp.Dispose();$memory.Dispose()'''
        fixture=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',script],capture_output=True,text=True,timeout=20,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        self.assertEqual(fixture.returncode,0)
        result=chart_ai.analyze({'image':fixture.stdout.strip()})['analysis']
        self.assertEqual(result['symbol'],'WHIRLPOOL',result['rawText'])
        self.assertEqual(result['timeframe'],'D',result['rawText'])
        self.assertEqual(result['setup'],'WIT',(result['warnings'],result['rawText']))
        self.assertEqual(result['entry'],731.45,result['rawText'])
        self.assertEqual(result['sl'],698.8,result['rawText'])
        self.assertEqual(result['target'],797.65,result['rawText'])
        self.assertTrue(result['usable'],(result['warnings'],result['rawText']))

    @unittest.skipUnless(os.name=='nt','Windows native OCR')
    def test_real_windows_reader_on_rendered_chart_labels(self):
        script = r'''Add-Type -AssemblyName System.Drawing
$bmp=New-Object System.Drawing.Bitmap(1400,900)
$g=[System.Drawing.Graphics]::FromImage($bmp)
$g.Clear([System.Drawing.Color]::AliceBlue)
$font=New-Object System.Drawing.Font('Arial',16)
$g.DrawString('CYIENT[NSE - 5748] D',$font,[System.Drawing.Brushes]::Black,5,5)
$g.DrawString('This is followup zone',$font,[System.Drawing.Brushes]::Black,500,280)
$g.DrawString('RB3-M',$font,[System.Drawing.Brushes]::Black,420,580)
$g.DrawString('Entry: 914.20',$font,[System.Drawing.Brushes]::Black,900,560)
$g.DrawString('Stop:879.10 Rs(-35.10)',$font,[System.Drawing.Brushes]::Red,900,620)
$memory=New-Object System.IO.MemoryStream
$bmp.Save($memory,[System.Drawing.Imaging.ImageFormat]::Png)
[Convert]::ToBase64String($memory.ToArray())
$g.Dispose();$font.Dispose();$bmp.Dispose();$memory.Dispose()'''
        fixture=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',script],capture_output=True,text=True,timeout=20,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        self.assertEqual(fixture.returncode,0)
        result=chart_ai.analyze({'image':fixture.stdout.strip()})['analysis']
        self.assertEqual(result['symbol'],'CYIENT',result['rawText'])
        self.assertEqual(result['entry'],914.2,result['rawText'])
        self.assertEqual(result['sl'],879.1,result['rawText'])
        self.assertEqual(result['setup'],'MIT',result['rawText'])
        self.assertEqual(result['timeframe'],'D',result['rawText'])
        self.assertIn('This is followup zone',result['notes'])
        bayer=script.replace('CYIENT[NSE - 5748] D','BAYERCROP[NSE - 17927] W').replace('RB3-M','RB1-H').replace('914.20','3339.10').replace('879.10','2848.30')
        bayer=bayer.replace('$memory=New-Object',"$g.DrawString('T:4357.70 Rs(1018.60)',$font,[System.Drawing.Brushes]::Green,900,450)\n$memory=New-Object")
        fixture=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',bayer],capture_output=True,text=True,timeout=20,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        result=chart_ai.analyze({'image':fixture.stdout.strip()})['analysis']
        self.assertEqual(result['setup'],'HYIT',result['rawText'])
        self.assertEqual(result['timeframe'],'W')
        self.assertTrue(result['usable'])

if __name__=='__main__':unittest.main()
