---
type: Release Verification
title: Launch probe retry nightly
description: Bounded GET catalog retry and truthful gate-message semantics with installed verification.
tags: [launch, timeout, sse, nightly]
timestamp: 2026-10-29
---
# Scope

User screenshot16shows launch blocked Base URL TimeoutError. Prior directly measured catalog200latency15.9/25.4s exceeds10sprobe; new bounded30sretryaftertimeoutonlyunderCielAiRouterpolicy preserves final failclosed behavior. ExactBettylatestworkspaceconfigdenied;do not claimremoteincidentcauseverified. Gatewordingclarificationdoesnotrelaxidentity. Source committed and pushed; exact CI/npm/install verification pending.

# Baseline and independent wording proof

Installed9077265against owned HTTP server delayed11s: probe timed out at10.0s with one GET and `Base URL: unreachable (TimeoutError)`. [Baseline](evidence/slow-catalog-baseline.json). No real provider generation used.

Gate policy/admission wording committedad36aebafter118focusedpass and actualCLIoff/status/onwith3screenshots,1baselineupstreamonly,0foregroundhits/804samples. This is not an identity-hold root-cause repair; grantlogs remain ACL-blocked.

# Source acceptance

Integrated source112/112focused tests pass with isolated config and safe subprocess imports. New provider retry tests verify20cases including both typed timeout forms, no HTTP/auth/TLS/DNS retry, at most2attempts, incomplete/oversized/malformed catalog failclosed, body-timeout classification, parsed-catalog context failure separation and unchanged other-provider policy. Only CielAiRouter opts into30sretryafterinitial10s.

The existing generic128KiBpreview truncated the measured389740bytecatalog; retry-enabled policy now parses at most1MiBwithoverflowdetection, otherwise failclosed. Socket operation timeouts are bounded, not an absolute total HTTP wall deadline under continuously trickling data.

# Actual source execution

Owned real HTTP server delays each catalog response11s. Baseline returns TimeoutError at10s; fixed policy succeeds via secondGETat21.016swithretry-successmessage. Actual nativeCLI launch with same11sdelay passes:3catalogGETs,1generationrequest,off/status/onlocalcommands,4PNGcaptures,0foregroundhits/1468samples. [Result](evidence/source-cli/result.json). Owned31sresponse-delaynegativeconfirmed:2GETsonly,40.016selapsed,unreachabletimeoutretained;launchgateisnotbypassed. No live router restarted.

# CI policy-budget correction

2ccfbafPublishfailedbeforepublication: ProviderStatusPolicy11dataclassfields exceeded existing10fielddependencybudget; no package installed. c1f9041movesretry to read-only provider-owned property, retaining10basefields.40focusedprovider/retry/architecturechecks pass. Corrected CI/Publish running at40999874715/40999874800.

# Corrected publication

Final commitc1f9041305e3db5671e5c21af64e410a1c3782b7passed CI40999874715and Publish40999874800. Published `0.2.51-nightly.20261030-040212.c1f9041`, exactgitHead/nightlytag/SHA1/SHA512/SHA256verified;SHA256e1f83c697683be56e55fa2eae34ddb37087056b22d6b1a17e87f44b23a21d53a. Publish unit3018/router1506/channel582/runtime455passed.

Corrected sourceCLI functionality also passed; one run observed owned console foreground2.23s/45samples. Cause not established. Original sourceCLI1468samples/0hits was clean; installed acceptance remains independently required and does not hide this limitation.

# Installed acceptance and local switch

Final c1f9041package517files match exact commit (507identical,8CRLF-only,2verifiedversionstamps); snapshot464files exact; global npm local-tarball install517files exact with scripts disabled. Installed112/112focusedtests pass with imports under exactsnapshot.

Actual installed nativeCLI with11sdelayedcatalog passed:3catalogGETs,1generationrequest,localoff/status/onnewresponses,4PNGframes,0foregroundhits/1518samples. [Result](evidence/installed-cli/result.json), [launch](evidence/installed-cli/01-gate-default-on.png), [on](evidence/installed-cli/gate-on.png). HKCU snapshot and generated statusline switched at2026-10-29T23:09:43-05:00. Normal local launch reports0.2.51-nightly.20261030-040212.c1f9041.

# Preservation limit

Whole-interval all-PID equality failed:9385changed89420→94220and9505changed68964→79700during this release interval, both now9077265 (not the newc1f9041). Cause not established. No restart requested/performed by this task; owned teardown excludes protectedPIDs. InstalledCLI itself saw9505PID79700/9524PID18536unchanged.8872PID76448and9524PID18536unchanged;4unrelateddirtyfileshashesunchanged. Preserve failed post-install comparison rather than report all-liveunchanged.

# Unresolved incident

SAMA grant identity hold root cause remains blocked by exactsessionassociationandACL-deniedlogs. Only confirmed wording confusion improved; identity/handoff safety not disabled. Bettylatestexactendpoint latencyalso unverified;the repaired slow-catalogclass is proven with prior real evidence and ownedloopback. No remote managed-host install/restart performed.
