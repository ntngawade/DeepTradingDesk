param([ValidateSet('status','read')][string]$Mode='status')
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false)
try {
    Add-Type -AssemblyName System.Runtime.WindowsRuntime
    $null=[Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime]
    $null=[Windows.Globalization.Language,Windows.Globalization,ContentType=WindowsRuntime]
    $engine=[Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage([Windows.Globalization.Language]::new('en-US'))
    if (-not $engine) { throw 'Windows English text recognition is unavailable on this account.' }
    if ($Mode -eq 'status') {
        @{available=$true;engine='Built-in Windows text recognition';language=$engine.RecognizerLanguage.LanguageTag} | ConvertTo-Json -Compress
        exit 0
    }
    $request=[Console]::In.ReadToEnd() | ConvertFrom-Json
    $bytes=[Convert]::FromBase64String($request.image)
    if ($bytes.Length -gt 5000000) { throw 'Chart image exceeds 5 MB.' }
    $null=[Windows.Storage.Streams.InMemoryRandomAccessStream,Windows.Storage.Streams,ContentType=WindowsRuntime]
    $null=[Windows.Storage.Streams.DataWriter,Windows.Storage.Streams,ContentType=WindowsRuntime]
    $null=[Windows.Graphics.Imaging.BitmapDecoder,Windows.Graphics.Imaging,ContentType=WindowsRuntime]
    Add-Type -AssemblyName System.Drawing
    Add-Type -ReferencedAssemblies System.Drawing -TypeDefinition @'
using System;
using System.Drawing;
using System.Drawing.Imaging;
using System.Runtime.InteropServices;
using System.Collections.Generic;
public static class ChartTextPixels {
    public static List<int[]> FindHorizontalAnnotations(Bitmap bitmap) {
        var results = new List<int[]>();
        var bounds = new Rectangle(0, 0, bitmap.Width, bitmap.Height);
        var data = bitmap.LockBits(bounds, ImageLockMode.ReadOnly, PixelFormat.Format32bppArgb);
        try {
            int width=bitmap.Width, height=bitmap.Height, threshold=Math.Max(160,width/7);
            byte[] row=new byte[Math.Abs(data.Stride)];
            int[] lastY={-100,-100,-100};
            int[] lastLength={0,0,0};
            int[] lastStart={0,0,0};
            for(int y=0;y<height;y++) {
                Marshal.Copy(IntPtr.Add(data.Scan0,y*data.Stride),row,0,row.Length);
                int runColor=0,runStart=0;
                int[] bestLength={0,0,0},bestStart={0,0,0};
                for(int x=0;x<=width;x++) {
                    int color=0;
                    if(x<width) {
                        int i=x*4,b=row[i],g=row[i+1],r=row[i+2],max=Math.Max(r,Math.Max(g,b));
                        if(r>135&&r>g*1.30&&r>b*1.25) color=1;
                        else if(g>125&&g>r*1.25&&g>b*1.20) color=2;
                        else if(max<68) color=3;
                    }
                    if(color!=runColor) {
                        if(runColor>0&&x-runStart>bestLength[runColor-1]) {bestLength[runColor-1]=x-runStart;bestStart[runColor-1]=runStart;}
                        runColor=color;runStart=x;
                    }
                }
                for(int c=0;c<3;c++) if(bestLength[c]>=threshold) {
                    if(y-lastY[c]<=7) {
                        if(bestLength[c]>lastLength[c]) {lastY[c]=y;lastLength[c]=bestLength[c];lastStart[c]=bestStart[c];}
                    } else {
                        results.Add(new int[]{c+1,y,bestStart[c],bestLength[c]});lastY[c]=y;lastLength[c]=bestLength[c];lastStart[c]=bestStart[c];
                    }
                }
            }
        } finally {bitmap.UnlockBits(data);}
        return results;
    }
    public static void Prepare(Bitmap bitmap, bool thin) {
        var bounds = new Rectangle(0, 0, bitmap.Width, bitmap.Height);
        var data = bitmap.LockBits(bounds, ImageLockMode.ReadWrite, PixelFormat.Format32bppArgb);
        try {
            int width = bitmap.Width, height = bitmap.Height;
            byte[] row = new byte[Math.Abs(data.Stride)];
            byte[] previous = new byte[row.Length];
            for (int i=0; i<previous.Length; i++) previous[i]=255;
            long brightness = 0; int count = 0;
            for (int y = 0; y < height; y += Math.Max(1, height / 16)) {
                Marshal.Copy(IntPtr.Add(data.Scan0, y * data.Stride), row, 0, row.Length);
                for (int x = 0; x < width; x += Math.Max(1, width / 16)) {
                    brightness += (row[x*4] + row[x*4+1] + row[x*4+2]) / 3; count++;
                }
            }
            bool dark = brightness / Math.Max(1, count) < 128;
            for (int y = 0; y < height; y++) {
                var address = IntPtr.Add(data.Scan0, y * data.Stride);
                Marshal.Copy(address, row, 0, row.Length);
                for (int x = 0; x < width; x++) {
                    int i = x*4;
                    bool ink = dark ? Math.Max(row[i], Math.Max(row[i+1], row[i+2])) > 160
                                    : (thin ? (row[i] + row[i+1] + row[i+2]) / 3 < 140 : Math.Min(row[i], Math.Min(row[i+1], row[i+2])) < 160);
                    row[i] = row[i+1] = row[i+2] = (byte)(ink ? 0 : 255); row[i+3] = 255;
                }
                // Remove long connected horizontal strokes, retaining separate
                // letter shapes and numbers. Original screenshots are untouched.
                int start = -1;
                for (int x = 0; x <= width; x++) {
                    if (x < width && row[x*4] == 0) { if (start < 0) start = x; }
                    else if (start >= 0) {
                        if (x-start >= 64) for (int j=start; j<x; j++) row[j*4] = row[j*4+1] = row[j*4+2] = (byte)(thin ? previous[j*4] : 255);
                        start = -1;
                    }
                }
                Marshal.Copy(row, 0, address, row.Length);
                Array.Copy(row, previous, row.Length);
            }
        } finally { bitmap.UnlockBits(data); }
    }
}
'@
    $asTask=[System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    } | Select-Object -First 1
    function Await-Operation($operation,[Type]$type) {
        $task=$asTask.MakeGenericMethod($type).Invoke($null,@($operation))
        $task.GetAwaiter().GetResult()
    }
    $stream=[Windows.Storage.Streams.InMemoryRandomAccessStream]::new()
    $writer=[Windows.Storage.Streams.DataWriter]::new($stream)
    $writer.WriteBytes($bytes)
    $null=Await-Operation ($writer.StoreAsync()) ([uint32])
    $stream.Seek(0)
    $decoder=Await-Operation ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    if ($decoder.PixelWidth -gt 16000 -or $decoder.PixelHeight -gt 16000 -or [double]$decoder.PixelWidth*$decoder.PixelHeight -gt 32000000) { throw 'Chart dimensions are too large. Use a normal screenshot.' }
    function Read-Region($regionDecoder,$offsetX,$offsetY,$regionWidth,$regionHeight,$zoom,$fullWidth,$fullHeight) {
    $scale=[Math]::Min($zoom,[double][Windows.Media.Ocr.OcrEngine]::MaxImageDimension/[Math]::Max($regionWidth,$regionHeight))
    $transform=[Windows.Graphics.Imaging.BitmapTransform]::new()
    $transform.ScaledWidth=[uint32][Math]::Max(1,[Math]::Floor($regionWidth*$scale))
    $transform.ScaledHeight=[uint32][Math]::Max(1,[Math]::Floor($regionHeight*$scale))
    $operation=$regionDecoder.GetSoftwareBitmapAsync([Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8,[Windows.Graphics.Imaging.BitmapAlphaMode]::Premultiplied,$transform,[Windows.Graphics.Imaging.ExifOrientationMode]::IgnoreExifOrientation,[Windows.Graphics.Imaging.ColorManagementMode]::DoNotColorManage)
    $bitmap=Await-Operation $operation ([Windows.Graphics.Imaging.SoftwareBitmap])
    $result=Await-Operation ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
    foreach ($line in $result.Lines) {
        $words=@(foreach ($word in $line.Words) {
            @{text=$word.Text;x=($offsetX+$word.BoundingRect.X/$transform.ScaledWidth*$regionWidth)/$fullWidth;y=($offsetY+$word.BoundingRect.Y/$transform.ScaledHeight*$regionHeight)/$fullHeight;width=$word.BoundingRect.Width/$transform.ScaledWidth*$regionWidth/$fullWidth;height=$word.BoundingRect.Height/$transform.ScaledHeight*$regionHeight/$fullHeight}
        })
        @{text=$line.Text;words=$words}
    }
    $bitmap.Dispose()
    }
    $fullWidth=[int]$decoder.PixelWidth;$fullHeight=[int]$decoder.PixelHeight
    $lines=[System.Collections.Generic.List[object]]::new()
    foreach ($line in (Read-Region $decoder 0 0 $fullWidth $fullHeight 2.5 $fullWidth $fullHeight)) {$line.scan='full';$lines.Add($line)}
    # Overlapping sections retain small price labels at 3x resolution even when
    # the full screenshot exceeds Windows OCR's maximum image dimension.
    $sourceStream=[System.IO.MemoryStream]::new($bytes,$false)
    $sourceBitmap=[System.Drawing.Bitmap]::new($sourceStream)
    try {
        foreach ($xFraction in @(0.0,0.23,0.46,0.68)) {
            foreach ($yFraction in @(0.0,0.32,0.60)) {
                $left=[int][Math]::Floor($fullWidth*$xFraction)
                $top=[int][Math]::Floor($fullHeight*$yFraction)
                $width=[int][Math]::Min([Math]::Ceiling($fullWidth*0.32),$fullWidth-$left)
                # Include the right-edge price axis and its adjacent colored labels.
                if ($xFraction -ge 0.68) { $left=[int][Math]::Floor($fullWidth*0.58);$width=[int][Math]::Min([Math]::Ceiling($fullWidth*0.42),$fullWidth-$left) }
                $height=[int][Math]::Min([Math]::Ceiling($fullHeight*0.40),$fullHeight-$top)
                foreach ($thin in @($true,$false)) {
                $crop=$sourceBitmap.Clone([System.Drawing.Rectangle]::new($left,$top,$width,$height),[System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
                [ChartTextPixels]::Prepare($crop,$thin)
                $cropMemory=[System.IO.MemoryStream]::new()
                $cropStream=[Windows.Storage.Streams.InMemoryRandomAccessStream]::new()
                $cropWriter=[Windows.Storage.Streams.DataWriter]::new($cropStream)
                try {
                    $crop.Save($cropMemory,[System.Drawing.Imaging.ImageFormat]::Png)
                    $cropWriter.WriteBytes($cropMemory.ToArray())
                    $null=Await-Operation ($cropWriter.StoreAsync()) ([uint32])
                    $cropStream.Seek(0)
                    $cropDecoder=Await-Operation ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($cropStream)) ([Windows.Graphics.Imaging.BitmapDecoder])
                    foreach ($line in (Read-Region $cropDecoder $left $top $width $height 3.0 $fullWidth $fullHeight)) {$line.scan='detail';$lines.Add($line)}
                } finally {$cropWriter.Dispose();$cropStream.Dispose();$cropMemory.Dispose();$crop.Dispose()}
                }
            }
        }
    # Marked entry/stop/target labels sit immediately beside long horizontal
    # colored lines. Read a narrow, enlarged region around each detected line
    # so tiny annotations remain legible without sending images anywhere.
    $lineMap32=$sourceBitmap.Clone([System.Drawing.Rectangle]::new(0,0,$fullWidth,$fullHeight),[System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
    $bands=[ChartTextPixels]::FindHorizontalAnnotations($lineMap32)
            $focus=@()
            foreach($band in $bands) {
                if($band[1] -lt [int]($fullHeight*0.08) -or $band[1] -gt [int]($fullHeight*0.92)) {continue}
                if(-not ($focus | Where-Object { [Math]::Abs($_[1]-$band[1]) -lt [Math]::Max(12,$fullHeight*0.018) })) {$focus+=,$band}
            }
            foreach($band in ($focus | Sort-Object { $_[3] } -Descending | Select-Object -First 8)) {
                $cropWidth=[int][Math]::Min($fullWidth,[Math]::Max(420,$fullWidth*0.34))
                $left=[int][Math]::Max(0,[Math]::Min($fullWidth-$cropWidth,$band[2]-$fullWidth*0.20))
                $cropHeight=[int][Math]::Min($fullHeight,[Math]::Max(90,$fullHeight*0.10))
                $top=[int][Math]::Max(0,[Math]::Min($fullHeight-$cropHeight,$band[1]-$cropHeight*0.72))
                foreach($thin in @($true,$false)) {
                    $crop=$lineMap32.Clone([System.Drawing.Rectangle]::new($left,$top,$cropWidth,$cropHeight),[System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
                    [ChartTextPixels]::Prepare($crop,$thin)
                    $cropMemory=[System.IO.MemoryStream]::new();$cropStream=[Windows.Storage.Streams.InMemoryRandomAccessStream]::new();$cropWriter=[Windows.Storage.Streams.DataWriter]::new($cropStream)
                    try {
                        $crop.Save($cropMemory,[System.Drawing.Imaging.ImageFormat]::Png);$cropWriter.WriteBytes($cropMemory.ToArray());$null=Await-Operation ($cropWriter.StoreAsync()) ([uint32]);$cropStream.Seek(0)
                        $cropDecoder=Await-Operation ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($cropStream)) ([Windows.Graphics.Imaging.BitmapDecoder])
                        foreach($line in (Read-Region $cropDecoder $left $top $cropWidth $cropHeight 5.0 $fullWidth $fullHeight)) {$line.scan='line-focus';$lines.Add($line)}
                    } finally {$cropWriter.Dispose();$cropStream.Dispose();$cropMemory.Dispose();$crop.Dispose()}
                }
            }
    } finally {$lineMap32.Dispose();$sourceBitmap.Dispose();$sourceStream.Dispose()}
    @{width=$fullWidth;height=$fullHeight;lines=$lines.ToArray();passes=41} | ConvertTo-Json -Depth 8 -Compress
    $writer.Dispose()
    $stream.Dispose()
} catch {
    @{error='Built-in chart text recognition failed. Check Windows English OCR support or try a clearer PNG/JPEG screenshot.'} | ConvertTo-Json -Compress
    exit 1
}
