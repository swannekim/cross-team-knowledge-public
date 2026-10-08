param([Parameter(Mandatory)][string]$BaseUrl,[Parameter(Mandatory)][string]$EvidencePath)
$ErrorActionPreference='Stop'
if($BaseUrl -ne 'https://broker.example.invalid'){throw 'Only the user-approved -2 demo endpoint is allowed.'}
$cases=@(
    @{id='B-LIVE-HEALTH';method='GET';path='/healthz';expected=200},
    @{id='B-LIVE-NO-TOKEN';method='POST';path='/ask';expected=401;body=@{question='How many seasoning wafers are required?';purpose='yield-excursion-analysis'}},
    @{id='B-LIVE-MALFORMED-TOKEN';method='POST';path='/ask';expected=401;body=@{question='How many seasoning wafers are required?';purpose='yield-excursion-analysis'};authorization='Bearer not-a-jwt'},
    @{id='B-LIVE-MCP-NO-TOKEN';method='POST';path='/mcp';expected=401;body=@{jsonrpc='2.0';id=1;method='initialize';params=@{protocolVersion='2025-06-18';capabilities=@{};clientInfo=@{name='KX synthetic test';version='1'}}}},
    @{id='B-LIVE-CITATION';method='GET';path='/access-request?ref=unknown-synthetic-reference';expected=200},
    @{id='B-LIVE-NOT-FOUND';method='GET';path='/not-a-route';expected=404}
)
$rows=@()
foreach($case in $cases){
    $headers=@{}
    if($case.authorization){$headers.Authorization=$case.authorization}
    $args=@{Uri=$BaseUrl+$case.path;Method=$case.method;Headers=$headers;SkipHttpErrorCheck=$true;TimeoutSec=90}
    if($case.body){$args.Body=$case.body|ConvertTo-Json -Depth 8 -Compress;$args.ContentType='application/json'}
    $timer=[Diagnostics.Stopwatch]::StartNew()
    $r=Invoke-WebRequest @args
    $timer.Stop()
    $row=@{id=$case.id;observedAt=[DateTimeOffset]::UtcNow.ToString('o');expected=$case.expected;actual=[int]$r.StatusCode;durationMs=$timer.ElapsedMilliseconds;passed=([int]$r.StatusCode -eq $case.expected)}
    if($case.id -eq 'B-LIVE-HEALTH'){$row.health=$r.Content|ConvertFrom-Json -AsHashtable}
    if($case.id -eq 'B-LIVE-CITATION'){
        $row.referenceNotReflected=(-not $r.Content.Contains('unknown-synthetic-reference'))
        $row.passed=$row.passed -and $row.referenceNotReflected
    }
    $rows+=$row
    Write-Output "$($row.id): HTTP $($row.actual), expected $($row.expected)"
}
$result=@{endpoint=$BaseUrl;scope='Unauthenticated HTTP and health only; not signed-in audience isolation or Copilot validation';tests=$rows;signedInTests='NOT EVALUATED BY THIS RUN: see separate delegated-user and Copilot evidence'}
$dir=Split-Path $EvidencePath -Parent
New-Item -ItemType Directory -Path $dir -Force|Out-Null
$result|ConvertTo-Json -Depth 12|Set-Content -LiteralPath $EvidencePath -Encoding utf8
if(@($rows|Where-Object {-not $_.passed}).Count){throw "Broker HTTP tests failed; see $EvidencePath"}
