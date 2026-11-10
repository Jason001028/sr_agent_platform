# Validate callbacks without installing a protocol or showing a native dialog.
$ErrorActionPreference='Stop'
$repoRoot=Split-Path $PSScriptRoot -Parent
$folder=Join-Path $PSScriptRoot ('fixtures\picker-'+[guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $folder -Force | Out-Null
try {
    $source=Join-Path $repoRoot 'frontend\public\client-explorer\Pick-SrImage.ps1'
    $tokens=$null;$errors=$null
    $ast=[Management.Automation.Language.Parser]::ParseFile($source,[ref]$tokens,[ref]$errors)
    if($errors){throw ($errors | Out-String)}
    # Exercise the actual production helper without installing or launching UI.
    $sampleFunction=$ast.Find({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Get-SrImageSamples'},$true)
    if(-not $sampleFunction){throw 'Missing production sampler'}
    . ([ScriptBlock]::Create($sampleFunction.Extent.Text))
    Add-Type -TypeDefinition @'
using System;
using System.IO;
using System.Collections.Generic;
public class SrLargeSampleStream : Stream {
    readonly long length; long position;
    public readonly List<long> Seeks = new List<long>();
    public long BytesRead;
    public SrLargeSampleStream(long length) { this.length=length; }
    public override bool CanRead { get { return true; } }
    public override bool CanSeek { get { return true; } }
    public override bool CanWrite { get { return false; } }
    public override long Length { get { return length; } }
    public override long Position { get { return position; } set { position=value; } }
    public override long Seek(long offset, SeekOrigin origin) {
        if(origin!=SeekOrigin.Begin || offset<0 || offset>length) throw new IOException();
        Seeks.Add(offset); return position=offset;
    }
    public override int Read(byte[] buffer,int offset,int count) {
        int n=(int)Math.Min((long)count,length-position);
        for(int i=0;i<n;i++) buffer[offset+i]=(byte)((position+i)%251);
        position+=n; BytesRead+=n; return n;
    }
    public override void Flush() {}
    public override void SetLength(long value) { throw new NotSupportedException(); }
    public override void Write(byte[] buffer,int offset,int count) { throw new NotSupportedException(); }
}
'@
    foreach($testLength in @(1,4095,4096,2147483648L,4294967297L,4894165908L,8589934592L)) {
        [long]$length=$testLength
        $stream=New-Object SrLargeSampleStream($length)
        try {
            $samples=Get-SrImageSamples $stream
            [long[]]$expected=@(0,[long][Math]::Floor([Math]::Max(0.0,($length-4096.0)/2.0)),[long][Math]::Max(0.0,($length-4096.0)))
            if($samples.Count -ne 3 -or $stream.Seeks.Count -ne 3){throw 'Expected three samples'}
            for($i=0;$i -lt 3;$i++) {
                if($stream.Seeks[$i] -ne $expected[$i]){throw "Wrong seek for length $length"}
                $bytes=[Convert]::FromBase64String($samples[$i])
                if($bytes.Length -ne [Math]::Min(4096.0,[double]$length)){throw 'Wrong sample size'}
                for($j=0;$j -lt $bytes.Length;$j++) {
                    if($bytes[$j] -ne (($expected[$i]+$j)%251)){throw 'Sample content mismatch'}
                }
            }
            if($stream.BytesRead -gt 12288){throw 'Unbounded fingerprint read'}
        } finally { $stream.Dispose() }
    }
    Write-Output 'PASS: real sampler, seven sizes through 8 GiB, Int64 seeks and bounded sample content.'
    Copy-Item -LiteralPath $source -Destination (Join-Path $folder 'Pick-SrImage.ps1')
    @{picker_origins=@('http://127.0.0.1:8766','http://10.10.81.135')} | ConvertTo-Json | Set-Content -Encoding UTF8 (Join-Path $folder 'mapping.json')
    $token='a'*64
    foreach($origin in @('http://127.0.0.1:8766','http://10.10.81.135:80')) {
        $callback=$origin+'/api/image-open/tickets/'+$token+'/selection'
        $actual=& (Join-Path $folder 'Pick-SrImage.ps1') -Callback $callback -ValidateOnly
        if($actual -cne $callback){throw 'Valid callback was not preserved'}
    }
    $invalidCallbacks=@(('http://evil.test/api/image-open/tickets/'+$token+'/selection'),
        ('file:///api/image-open/tickets/'+$token+'/selection'),
        ('http://127.0.0.1:8766/api/image-open/tickets/'+$token+'/selection?redirect=evil'),
        ('http://user@127.0.0.1:8766/api/image-open/tickets/'+$token+'/selection'),
        'http://127.0.0.1:8766/api/image-open/tickets/bad/selection')
    if($invalidCallbacks.Count -ne 5){throw 'Expected five independent invalid callbacks'}
    foreach($callback in $invalidCallbacks) {
        $rejected=$false
        try{& (Join-Path $folder 'Pick-SrImage.ps1') -Callback $callback -ValidateOnly | Out-Null}catch{$rejected=$true}
        if(-not $rejected){throw "Accepted invalid callback: $callback"}
    }
    Write-Output 'PASS: callback origin, token, query, scheme and credentials validation; no install or file dialog.'
}finally{
    Remove-Item -LiteralPath (Join-Path $folder 'Pick-SrImage.ps1'),(Join-Path $folder 'mapping.json') -Force
    Remove-Item -LiteralPath $folder
}
