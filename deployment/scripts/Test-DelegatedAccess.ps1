param(
    [Parameter(Mandatory)][ValidateSet('admin','reader','outsider')][string]$Identity,
    [switch]$SelectedUser,
    [switch]$PersonalBrokerInstall,
    [Parameter(Mandatory)][string]$EvidencePath
)
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'Demo.psm1') -Force
$state = Get-DemoState
$origin = 'https://broker.example.invalid'
if ($state.broker.url -ne $origin) { throw 'Unexpected broker origin.' }
$expectedUpn = if ($Identity -eq 'admin') {'admin@example.invalid'} else {$state.users[$Identity].upn}
$expectedId = if ($Identity -eq 'admin') {'f0000000-0000-4000-8000-000000000031'} else {$state.users[$Identity].id}
if ($SelectedUser) {
    if ($Identity -eq 'admin' -or -not $state.selectedTestUsers[$Identity]) {
        throw 'SelectedUser requires a separately recorded reader or outsider identity.'
    }
    $selected = $state.selectedTestUsers[$Identity]
    if (-not $selected.id -or $selected.upn -notmatch '^[^@]+@example\.invalid$') {
        throw 'Selected test identity must be a recorded user in the approved example tenant.'
    }
    $expectedUpn = $selected.upn
    $expectedId = $selected.id
}
if ($PersonalBrokerInstall) {
    $installationUsers = @{
        reader=@{id='f0000000-0000-4000-8000-00000000001a';upn='Reader@example.invalid'}
        outsider=@{id='f0000000-0000-4000-8000-000000000005';upn='Outsider@example.invalid'}
    }
    if (-not $SelectedUser -or -not $installationUsers.ContainsKey($Identity) -or
        $expectedId -ne $installationUsers[$Identity].id -or $expectedUpn -ne $installationUsers[$Identity].upn -or
        $state.tenantId -ne 'f0000000-0000-4000-8000-000000000019' -or
        $state.apps.Reader.clientId -ne 'f0000000-0000-4000-8000-000000000018') {
        throw 'PersonalBrokerInstall is restricted to the two approved existing test identities and client.'
    }
}
$authority = "https://login.microsoftonline.com/$($state.tenantId)/oauth2/v2.0"
$result = [ordered]@{
    startedAt = [DateTimeOffset]::UtcNow.ToString('o')
    identity = $expectedUpn
    testRole = $Identity
    selectedExistingUser = [bool]$SelectedUser
    scope = 'Real delegated Graph and broker API checks; not Copilot UI or ordinary-user proof when identity is admin.'
    authentication = 'PENDING'
    runStatus = 'RUNNING'
    phase = 'AUTHENTICATION'
    tests = [Collections.Generic.List[object]]::new()
}
if ($PersonalBrokerInstall) {
    $result.scope = 'Existing broker personal installation through delegated Graph only; not a Copilot invocation or broker authorization test.'
    $result.installation = @{status='NOT_ATTEMPTED';appId='f0000000-0000-4000-8000-000000000017'}
}
$graphToken = $null
$brokerToken = $null
$tokens = $null

function Invoke-CheckedHttp([string]$Uri, [string]$Token, [string]$Method = 'GET', $Body) {
    $target = [uri]$Uri
    if ($target.Scheme -ne 'https' -or $target.Host -notin @('graph.microsoft.com', ([uri]$origin).Host)) {
        throw 'Only Graph and the approved broker are allowed.'
    }
    $args = @{Uri=$Uri;Method=$Method;Headers=@{Authorization="Bearer $Token"};SkipHttpErrorCheck=$true;TimeoutSec=90;MaximumRedirection=0}
    if ($null -ne $Body) {
        $args.ContentType = 'application/json'
        $args.Body = $Body | ConvertTo-Json -Depth 15 -Compress
    }
    $response = Invoke-WebRequest @args
    $parsed = if ($response.Content) {$response.Content | ConvertFrom-Json -AsHashtable} else {$null}
    $requestId = [string]($response.Headers['request-id'] | Select-Object -First 1)
    if (-not $requestId) {$requestId=[string]($response.Headers['X-Request-Id'] | Select-Object -First 1)}
    @{status=[int]$response.StatusCode;body=$parsed;requestId=$requestId;observedAt=[DateTimeOffset]::UtcNow.ToString('o')}
}

function Record-Status([string]$Id, $Response, [int[]]$Expected) {
    $row = [ordered]@{id=$Id;observedAt=$Response.observedAt;expectedStatus=$Expected;actualStatus=$Response.status;passed=($Response.status -in $Expected);requestId=$Response.requestId}
    if ($Response.body.error) {$row.errorCode=$Response.body.error.code}
    $result.tests.Add($row)
    Write-Host "$Id : HTTP $($Response.status); passed=$($row.passed)"
    return $row
}

function Save-DelegatedEvidence {
    $directory = Split-Path -Parent $EvidencePath
    if ($directory) {New-Item -ItemType Directory -Path $directory -Force | Out-Null}
    $result | ConvertTo-Json -Depth 30 | Set-Content -LiteralPath $EvidencePath -Encoding utf8
}

function Assert-InstallationWindow {
    if ([DateTimeOffset]::UtcNow -ge [DateTimeOffset]'2026-10-08T00:00:00Z') {
        throw 'The bounded personal-install attempt ends at 8 October 2026 09:00 KST.'
    }
}

function Invoke-PersonalBrokerInstallation {
    Assert-InstallationWindow
    $appId = 'f0000000-0000-4000-8000-000000000017'
    $manifestId = 'f0000000-0000-4000-8000-000000000024'
    $base = 'https://graph.microsoft.com/v1.0'
    $result.phase = 'APP_CATALOG_PREFLIGHT'
    $catalogUri = "$base/appCatalogs/teamsApps/${appId}?`$select=id,externalId,displayName,distributionMethod&`$expand=appDefinitions(`$select=id,teamsAppId,version,publishingState)"
    $response = Invoke-CheckedHttp $catalogUri $graphToken
    $row = Record-Status 'B-EXISTING-APP-CATALOG' $response @(200)
    if (-not $row.passed) {
        $result.installation.status = 'BLOCKED_CATALOG'
        throw 'The exact existing broker app could not be read from this user catalog; no installation attempted.'
    }
    $app = $response.body
    $row.app = $app
    $definitions = @($app.appDefinitions | Where-Object {
        $_ -is [System.Collections.IDictionary] -and $_.teamsAppId -eq $appId -and $_.version -ceq '1.0.0'
    })
    $row.passed = ($app.id -eq $appId -and $app.externalId -eq $manifestId -and
        $app.displayName -ceq 'KX synthetic knowledge broker' -and
        $app.appDefinitions -is [System.Collections.IList] -and $app.appDefinitions.Count -eq 1 -and
        -not $app.ContainsKey('appDefinitions@odata.nextLink') -and -not $app.ContainsKey('@odata.nextLink') -and
        $definitions.Count -eq 1 -and $definitions[0].id -and $definitions[0].publishingState -ceq 'published')
    if (-not $row.passed) {
        $result.installation.status = 'BLOCKED_APP_MISMATCH'
        throw 'Existing app identity, manifest, name or version differs from approval; refusing installation.'
    }
    $installedUri = "$base/users/$expectedId/teamwork/installedApps?`$filter=teamsApp/id%20eq%20%27$appId%27&`$expand=teamsApp(`$select=id,externalId),teamsAppDefinition(`$select=id,version)"
    $response = Invoke-CheckedHttp $installedUri $graphToken
    $row = Record-Status 'B-PERSONAL-INSTALL-PREFLIGHT' $response @(200)
    if (-not $row.passed -or $response.body.value -isnot [System.Collections.IList]) {
        $row.passed = $false
        throw 'Could not read existing personal installation state; refusing a blind install.'
    }
    if ($response.body.ContainsKey('@odata.nextLink')) {
        $row.passed = $false
        throw 'Unexpected paged filtered installation result; refusing an incomplete preflight.'
    }
    $entries = @($response.body.value | Where-Object {$_.teamsApp.id -eq $appId})
    if ($entries.Count -gt 1 -or $response.body.value.Count -ne $entries.Count) {
        $row.passed = $false
        throw 'Ambiguous or unfiltered personal installation state; no mutation attempted.'
    }
    $result.installation.alreadyInstalled = ($entries.Count -eq 1)
    if (-not $entries.Count) {
        Assert-InstallationWindow
        $result.phase = 'PERSONAL_INSTALL'
        $result.installation.status = 'SUBMISSION_PENDING'
        $result.installation.mutationAttempted = $true
        Save-DelegatedEvidence
        $response = Invoke-CheckedHttp "$base/users/$expectedId/teamwork/installedApps" $graphToken 'POST' @{
            'teamsApp@odata.bind'="$base/appCatalogs/teamsApps/$appId"
        }
        $row = Record-Status 'B-PERSONAL-INSTALL-SUBMISSION' $response @(201,409)
        $row.note = 'A 409 requires readback; it is not an installation success by itself.'
        if (-not $row.passed) {
            $result.installation.status = 'SUBMISSION_FAILED'
            throw 'Existing broker personal installation was not accepted; no fallback upload or app creation attempted.'
        }
        $result.installation.status = 'SUBMITTED_NOT_VERIFIED'
    }
    $result.phase = 'PERSONAL_INSTALL_READBACK'
    for ($attempt=0; $attempt -lt 6; $attempt++) {
        $response = Invoke-CheckedHttp $installedUri $graphToken
        if ($response.status -ne 200) {break}
        if ($response.body.value -isnot [System.Collections.IList] -or $response.body.ContainsKey('@odata.nextLink')) {
            throw 'Malformed or incomplete installation readback; no verified success is claimed.'
        }
        $entries = @($response.body.value | Where-Object {
            $_.id -and $_.teamsApp.id -eq $appId -and $_.teamsApp.externalId -eq $manifestId -and
            $_.teamsAppDefinition.version -ceq '1.0.0'
        })
        if ($entries.Count -eq 1 -and $response.body.value.Count -eq 1) {break}
        if ($attempt -lt 5) {Start-Sleep -Seconds 5}
    }
    $row = Record-Status 'B-PERSONAL-INSTALL-READBACK' $response @(200)
    $row.passed = ($response.status -eq 200 -and $entries.Count -eq 1 -and $response.body.value.Count -eq 1)
    if (-not $row.passed) {
        throw 'Personal installation was not verified; preserve the submission record and do not retry blindly.'
    }
    $result.installation.status = 'VERIFIED'
    $result.installation.installationId = $entries[0].id
    $result.installation.verifiedAt = $response.observedAt
    $result.installation.copilotInvocation = 'NOT_RUN'
}

function Test-ValidCitations($Citations) {
    if ($Citations -isnot [System.Collections.IList] -or $Citations.Count -eq 0) {return $false}
    foreach ($citation in $Citations) {
        if ($citation -isnot [System.Collections.IDictionary]) {return $false}
        foreach ($field in @('ref','title','sourceTeam','sensitivity','accessRequestUrl','excerpt')) {
            if ($citation[$field] -isnot [string] -or [string]::IsNullOrWhiteSpace($citation[$field])) {return $false}
        }
        $link = $null
        if ($citation.ref -cnotmatch '^ref-[0-9a-f]{24}$' -or
            $citation.sensitivity -notin @('Public','General','Confidential') -or
            -not [uri]::TryCreate($citation.accessRequestUrl,[UriKind]::Absolute,[ref]$link) -or
            $link.Scheme -ne 'https' -or $link.Host -ne ([uri]$origin).Host) {return $false}
    }
    return $true
}

function Test-CitedAnswer($Body) {
    return ($Body -is [System.Collections.IDictionary] -and -not $Body.Contains('error') -and
        $Body.answer -is [string] -and -not [string]::IsNullOrWhiteSpace($Body.answer) -and
        (Test-ValidCitations $Body.citations))
}

function Test-CitedMcpResult($Body) {
    return ($Body -is [System.Collections.IDictionary] -and -not $Body.Contains('error') -and
        $Body.jsonrpc -ceq '2.0' -and $Body.id -eq 1 -and
        $Body.result -is [System.Collections.IDictionary] -and
        $Body.result.isError -is [bool] -and $Body.result.isError -eq $false -and
        (Test-CitedAnswer $Body.result.structuredContent))
}

try {
    if ($PersonalBrokerInstall) {Assert-InstallationWindow}
    $graphScope = if ($PersonalBrokerInstall) {
        'https://graph.microsoft.com/User.Read https://graph.microsoft.com/AppCatalog.Read.All https://graph.microsoft.com/TeamsAppInstallation.ReadWriteForUser'
    } else {'https://graph.microsoft.com/.default offline_access'}
    $flow = Invoke-RestMethod -Method Post -Uri "$authority/devicecode" -Body @{
        client_id=$state.apps.Reader.clientId
        scope=$graphScope
    }
    Write-Output "Sign in ONLY as $expectedUpn"
    Write-Output "Verification URL: $($flow.verification_uri)"
    Write-Output "User code: $($flow.user_code)"
    Write-Output "Expires in $($flow.expires_in) seconds. Tokens remain in process memory."
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($flow.expires_in)
    $interval = [int]$flow.interval
    while ([DateTimeOffset]::UtcNow -lt $deadline) {
        Start-Sleep -Seconds $interval
        $response = Invoke-WebRequest -Method Post -Uri "$authority/token" -SkipHttpErrorCheck -Body @{
            client_id=$state.apps.Reader.clientId
            grant_type='urn:ietf:params:oauth:grant-type:device_code'
            device_code=$flow.device_code
        }
        $candidate = $response.Content | ConvertFrom-Json -AsHashtable
        if ($response.StatusCode -eq 200) {$tokens=$candidate;break}
        if ($candidate.error -eq 'authorization_pending') {continue}
        if ($candidate.error -eq 'slow_down') {$interval+=5;continue}
        $result.authenticationFailure = @{
            error=$candidate.error
            errorCodes=$candidate.error_codes
            correlationId=$candidate.correlation_id
            traceId=$candidate.trace_id
            timestamp=$candidate.timestamp
            suberror=$candidate.suberror
            claimsChallengePresent=[bool]$candidate.claims
        }
        throw "Device authentication failed: $($candidate.error); codes=$($candidate.error_codes -join ',')"
    }
    if (-not $tokens) {throw 'Device authentication expired without completion.'}
    $graphToken = $tokens.access_token
    $me = Invoke-CheckedHttp 'https://graph.microsoft.com/v1.0/me?$select=id,userPrincipalName' $graphToken
    if ($me.status -ne 200 -or $me.body.id -ne $expectedId -or $me.body.userPrincipalName -ne $expectedUpn) {
        throw 'Signed-in identity does not match the requested demo identity; no data tests executed.'
    }
    $result.authentication = 'PASS'
    $result.phase = 'GRAPH_CHECKS'
    $result.verifiedObjectId = $me.body.id
    Write-Output "Verified delegated identity: $expectedUpn"
    if ($PersonalBrokerInstall) {
        Invoke-PersonalBrokerInstallation
        $result.completedAt = [DateTimeOffset]::UtcNow.ToString('o')
        $result.phase = 'COMPLETE'
        $result.runStatus = 'PASSED'
        return
    }

    $source = $state.sources['GL-ETCH-007_chamber_seasoning_guideline.txt']
    if (-not $source.id) {throw 'Registered synthetic source is missing.'}
    $base = 'https://graph.microsoft.com/v1.0'
    $response = Invoke-CheckedHttp "$base/drives/$($state.sites.Source.driveId)/items/$($source.id)?`$select=id,name" $graphToken
    $null = Record-Status 'ORIGINAL-METADATA-DENIED' $response @(403,404)
    $response = Invoke-CheckedHttp "$base/drives/$($state.sites.Source.driveId)/root/children?`$select=id,name" $graphToken
    $null = Record-Status 'ORIGINAL-LIST-DENIED' $response @(403,404)
    $allowed = $Identity -ne 'outsider'
    $response = Invoke-CheckedHttp "$base/drives/$($state.sites.Exchange.driveId)/root/children?`$select=id,name" $graphToken
    $expected = if ($allowed) {@(200)} else {@(403,404)}
    $null = Record-Status 'EXCHANGE-LIST-ACCESS' $response $expected
    if ($response.status -eq 200) {
        $result.exchangePublishedCardsSeen = @($response.body.value | Where-Object {$_.name -like 'kx-*-sum0000.txt'}).Count
    }

    foreach ($architecture in @('A','C')) {
        $request = if ($architecture -eq 'A') {
            @{entityTypes=@('externalItem');contentSources=@("/external/connections/$($state.connectionId)");query=@{queryString='"GL-ETCH-007"'};from=0;size=25}
        } else {
            @{entityTypes=@('driveItem');query=@{queryString='"GL-ETCH-007" path:"https://sharepoint.example.invalid/sites/Example-Exchange"'};from=0;size=25}
        }
        $response = Invoke-CheckedHttp "$base/search/query" $graphToken 'POST' @{requests=@($request)}
        $hits = @()
        if ($response.status -eq 200) {
            foreach ($value in $response.body.value) {
                foreach ($container in $value.hitsContainers) {
                    if ($container.hits) {$hits += @($container.hits)}
                }
            }
        }
        $row = [ordered]@{
            id="$architecture-DELEGATED-SEARCH"
            observedAt=$response.observedAt
            actualStatus=$response.status
            hitCount=$hits.Count
            expected= $(if($allowed){'At least one scoped synthetic result'}else{'Successful query with zero scoped results'})
            passed=($response.status -eq 200 -and $(if($allowed){$hits.Count -gt 0}else{$hits.Count -eq 0}))
            requestId=$response.requestId
        }
        if ($response.body.error) {$row.errorCode=$response.body.error.code}
        $result.tests.Add($row)
        Write-Output "$($row.id): HTTP $($row.actualStatus), hits=$($row.hitCount), passed=$($row.passed)"
    }

    $result.phase = 'BROKER_AUTHENTICATION'
    $response = Invoke-WebRequest -Method Post -Uri "$authority/token" -SkipHttpErrorCheck -Body @{
        client_id=$state.apps.Reader.clientId
        grant_type='refresh_token'
        refresh_token=$tokens.refresh_token
        scope="api://$($state.apps.Broker.clientId)/Knowledge.Ask"
    }
    $brokerAuth = $response.Content | ConvertFrom-Json -AsHashtable
    if ($response.StatusCode -ne 200) {
        $result.brokerAuthenticationFailure = @{
            error=$brokerAuth.error
            errorCodes=$brokerAuth.error_codes
            correlationId=$brokerAuth.correlation_id
            traceId=$brokerAuth.trace_id
            timestamp=$brokerAuth.timestamp
            claimsChallengePresent=[bool]$brokerAuth.claims
        }
        throw "Broker token acquisition failed: $($brokerAuth.error); codes=$($brokerAuth.error_codes -join ',')"
    }
    $brokerToken = $brokerAuth.access_token
    $result.phase = 'BROKER_CHECKS'
    $question = 'How many seasoning wafers are required after a wet clean only?'
    $response = Invoke-CheckedHttp "$origin/ask" $brokerToken 'POST' @{question=$question;purpose='yield-excursion-analysis'}
    $row = Record-Status 'B-DELEGATED-ASK' $response $(if($allowed){@(200)}else{@(403)})
    $row.citationCount = @($response.body.citations | Where-Object {$null -ne $_}).Count
    $row.response = $response.body
    if ($allowed) {
        $row.passed = $row.passed -and (Test-CitedAnswer $response.body)
        $result.tests.Add([ordered]@{
            id='B-WET-CLEAN-ANSWER'
            observedAt=$response.observedAt
            expected='15 seasoning wafers after a wet clean only, supported by the approved guideline'
            assertion='The returned answer contains the expected wafer count and valid supporting citations.'
            passed=($row.passed -and $response.body.answer -match '(?i)\b15\s+(?:seasoning\s+)?wafers\b')
        })
    } else {
        $row.passed = $row.passed -and $row.citationCount -eq 0
    }
    $response = Invoke-CheckedHttp "$origin/mcp" $brokerToken 'POST' @{
        jsonrpc='2.0';id=1;method='tools/call'
        params=@{name='askKnowledge';arguments=@{question=$question;purpose='yield-excursion-analysis'}}
    }
    $row = Record-Status 'B-DELEGATED-MCP' $response $(if($allowed){@(200)}else{@(403)})
    $row.response = $response.body
    if ($allowed) {$row.passed=$row.passed -and (Test-CitedMcpResult $response.body)}
    else {
        $row.passed=$row.passed -and (-not $response.body.citations) -and (-not $response.body.result.structuredContent.citations)
    }
    if ($allowed) {
        $response = Invoke-CheckedHttp "$origin/ask" $brokerToken 'POST' @{
            question='What is the planned Q4 2026 maintenance date and duration for ETCH-07 chamber B?'
            purpose='yield-excursion-analysis'
        }
        $row = Record-Status 'B-MAINTENANCE-ANSWER' $response @(200)
        $row.response = $response.body
        $row.expected = '2026-10-21, duration 18 hours, supported by the returned maintenance excerpt'
        $row.passed = $row.passed -and (Test-CitedAnswer $response.body) -and $response.body.answer -match '2026-10-21' -and $response.body.answer -match '\b18\b'
        $response = Invoke-CheckedHttp "$origin/ask" $brokerToken 'POST' @{
            question='What is the planned Q4 2026 maintenance date for ETCH-07 chamber B?'
            purpose='unapproved-demo-purpose'
        }
        $row = Record-Status 'B-UNAPPROVED-PURPOSE' $response @(403)
        $row.response = $response.body
        $row.passed = $row.passed -and (-not $response.body.citations)
    }
    $result.completedAt = [DateTimeOffset]::UtcNow.ToString('o')
    $result.phase = 'COMPLETE'
    $result.runStatus = if (@($result.tests | Where-Object {-not $_.passed}).Count) {'FAILED'} else {'PASSED'}
} catch {
    if ($result.authentication -eq 'PENDING') {$result.authentication='FAILED'}
    $result.runStatus = if ($result.tests.Count) {'INCOMPLETE'} else {'FAILED'}
    $result.failedAt = [DateTimeOffset]::UtcNow.ToString('o')
    $result.failure = $_.Exception.Message
    throw
} finally {
    $graphToken=$null;$brokerToken=$null;$tokens=$null;$flow=$null;$candidate=$null;$brokerAuth=$null
    Save-DelegatedEvidence
}
if (@($result.tests | Where-Object {-not $_.passed}).Count) {throw "Delegated checks did not all pass; see $EvidencePath"}
