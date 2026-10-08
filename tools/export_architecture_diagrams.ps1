$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
$root = Split-Path $PSScriptRoot -Parent
$directory = Join-Path $root 'docs\diagrams'
$names = @('architecture-a-connector', 'architecture-b-broker', 'architecture-c-publishing')

foreach ($name in $names) {
    $source = Get-Content -Raw -LiteralPath (Join-Path $directory "$name.excalidraw") | ConvertFrom-Json
    $svg = [xml](Get-Content -Raw -LiteralPath (Join-Path $directory "$name.svg"))
    $bitmap = [Drawing.Bitmap]::new([int]$svg.svg.width, [int]$svg.svg.height)
    $graphics = [Drawing.Graphics]::FromImage($bitmap)
    $graphics.SmoothingMode = [Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $graphics.TextRenderingHint = [Drawing.Text.TextRenderingHint]::AntiAliasGridFit
    $graphics.Clear([Drawing.Color]::White)
    try {
        foreach ($element in $source.elements) {
            switch ($element.type) {
                'rectangle' {
                    $pen = [Drawing.Pen]::new([Drawing.ColorTranslator]::FromHtml($element.strokeColor), 2)
                    $brush = [Drawing.SolidBrush]::new([Drawing.ColorTranslator]::FromHtml($element.backgroundColor))
                    $path = [Drawing.Drawing2D.GraphicsPath]::new()
                    try {
                        $x = [single]$element.x; $y = [single]$element.y
                        $w = [single]$element.width; $h = [single]$element.height
                        $r = [single]24
                        $path.AddArc($x, $y, $r, $r, 180, 90)
                        $path.AddArc($x + $w - $r, $y, $r, $r, 270, 90)
                        $path.AddArc($x + $w - $r, $y + $h - $r, $r, $r, 0, 90)
                        $path.AddArc($x, $y + $h - $r, $r, $r, 90, 90)
                        $path.CloseFigure()
                        $graphics.FillPath($brush, $path)
                        $graphics.DrawPath($pen, $path)
                    } finally {
                        $path.Dispose(); $brush.Dispose(); $pen.Dispose()
                    }
                }
                'text' {
                    $style = if ($element.fontSize -ge 23) {[Drawing.FontStyle]::Bold} else {[Drawing.FontStyle]::Regular}
                    $font = [Drawing.Font]::new('Arial', [single]$element.fontSize, $style, [Drawing.GraphicsUnit]::Pixel)
                    $format = [Drawing.StringFormat]::GenericTypographic.Clone()
                    $format.FormatFlags = $format.FormatFlags -bor [Drawing.StringFormatFlags]::NoWrap
                    try {
                        $lineIndex = 0
                        foreach ($line in $element.text.Split("`n")) {
                            $size = $graphics.MeasureString($line, $font, [int]2000, $format)
                            if ($size.Width -gt $element.width + 1) {
                                throw "Diagram text exceeds its available width: $name / $line"
                            }
                            $point = [Drawing.PointF]::new([single]$element.x, [single]($element.y + $lineIndex * $element.fontSize * 1.4))
                            $graphics.DrawString($line, $font, [Drawing.Brushes]::Black, $point, $format)
                            $lineIndex++
                        }
                    } finally {
                        $format.Dispose(); $font.Dispose()
                    }
                }
                'arrow' {
                    $pen = [Drawing.Pen]::new([Drawing.ColorTranslator]::FromHtml($element.strokeColor), [single]2.5)
                    $cap = [Drawing.Drawing2D.AdjustableArrowCap]::new(4, 6, $true)
                    try {
                        $pen.CustomEndCap = $cap
                        if ($element.startArrowhead -eq 'arrow') {$pen.CustomStartCap = $cap}
                        if ($element.strokeStyle -eq 'dashed') {$pen.DashStyle = [Drawing.Drawing2D.DashStyle]::Dash}
                        $points = [Drawing.PointF[]]@($element.points | ForEach-Object {
                            [Drawing.PointF]::new([single]($element.x + $_[0]), [single]($element.y + $_[1]))
                        })
                        $graphics.DrawLines($pen, $points)
                    } finally {
                        $cap.Dispose(); $pen.Dispose()
                    }
                }
                default {throw "Unsupported diagram element: $($element.type)"}
            }
        }
        $destination = Join-Path $directory "$name.png"
        $bitmap.Save($destination, [Drawing.Imaging.ImageFormat]::Png)
        Write-Output "Rendered $destination"
    } finally {
        $graphics.Dispose(); $bitmap.Dispose()
    }
}
