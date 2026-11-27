; VC_VERSION: 1.3.6
; Stamp must match the vcli binary's `Cargo.toml` version. `vcli session show`
; compares this with the daemon's `VCDVersion` global (parsed from the
; `VERSION:x.x.x` line the daemon prints to stderr on startup). If you see a
; version_skew warning, either:
;   (a) reload this .il after upgrading vcli, or
;   (b) reinstall vcli (`cargo install --path .` or pull the matching release).
;
;RB = RAMIC Bridge
;VCD = RB Daemon
;VCM = RB Monitor

; Daemon binary path: resolved at load-time via an ordered search chain,
; never hardcoded. sh() returns t/nil (not stdout), so `which` cannot be used
; for path detection — candidates are probed with isFile() instead.
; Search order (first existing wins):
;   (1) __DAEMON_PATH__  deploy-time substitution (vcli tunnel start / install.sh)
;   (2) VC_DAEMON_PATH   explicit env override
;   (3) /opt/virtuoso-cli/bin, /usr/local/bin   shared multi-user installs
;   (4) ~/.local/bin, ~/.cargo/bin              per-user installs (back-compat)
; Multi-user friendly: one shared install (e.g. /opt/virtuoso-cli/bin) is found
; by every user with zero per-user setup. Re-resolved on reload if previously
; empty or the file no longer exists.
procedure(VCResolveDaemonPath()
    let((home deployPath envPath cand candidates result)
        home = getShellEnvVar("HOME")
        unless(home home = "")
        result = ""
        ; (1) Deploy-time substitution. `vcli tunnel start` and install.sh replace
        ;     the __DAEMON_PATH__ token with an absolute path. When the .il is loaded
        ;     directly the token stays literal, so isFile() is nil and it is skipped
        ;     — no split-literal trick needed since it is never a real file.
        deployPath = "__DAEMON_PATH__"
        when(result == "" && isFile(deployPath) result = deployPath)
        ; (2) Explicit env override (now actually honored — _VCAutoStart no longer
        ;     clobbers it).
        envPath = getShellEnvVar("VC_DAEMON_PATH")
        when(result == "" && envPath && isFile(envPath) result = envPath)
        ; (3)+(4) Shared installs first (multi-user), then per-user (compat).
        candidates = list(
            "/opt/virtuoso-cli/bin/virtuoso-daemon"
            "/usr/local/bin/virtuoso-daemon"
            strcat(home "/.local/bin/virtuoso-daemon")
            strcat(home "/.cargo/bin/virtuoso-daemon")
        )
        foreach(cand candidates
            when(result == "" && isFile(cand) result = cand))
        result
    )
)
when(!boundp('VCDPath) || VCDPath == "" || VCDPath == nil || !isFile(VCDPath)
    VCDPath = VCResolveDaemonPath()
)
when(VCDPath == "" || VCDPath == nil
    printf("[RAMIC Bridge] ERROR: virtuoso-daemon not found in any known location.\n")
    printf("[RAMIC Bridge] Searched (in order): deploy path, $VC_DAEMON_PATH, /opt/virtuoso-cli/bin, /usr/local/bin, ~/.local/bin, ~/.cargo/bin\n")
    printf("[RAMIC Bridge] Fix: run install.sh (shared/multi-user), set VC_DAEMON_PATH, or cargo install --path <repo> --bin virtuoso-daemon --features daemon\n")
)
; VCPython is no longer used (daemon is a standalone binary), kept for compatibility
unless(boundp('VCPython) VCPython = "")
; All state vars preserved across reloads — only initialized if not yet bound.
; VCPort must NOT be reset on reload: the running daemon's actual port would be lost.
unless(boundp('VCPort)       VCPort       = 0)   ; 0 = OS assigns; updated by VCIpcErrHandler
unless(boundp('VCLocal)      VCLocal      = nil)
unless(boundp('VCEcho)       VCEcho       = nil)
unless(boundp('VCDLog)       VCDLog       = nil)
unless(boundp('VCShowBanner) VCShowBanner = nil) ; t = print banner when next PORT: arrives
unless(boundp('VCDVersion)   VCDVersion   = "")  ; set from VERSION: line before PORT:

; IPC process handle — must survive reload so we don't spawn a second daemon.
unless(boundp('VCIpc) VCIpc = 'unbound)

; Session management — persist across reloads so session_id stays stable
unless(boundp('VCSessionId)   VCSessionId   = "")
; hostname-user prefix; VCIpcErrHandler appends the OS-assigned port to form the final unique ID
unless(boundp('VCSessionBase) VCSessionBase = "")
; SSH port for tunnel connection — re-read from env on every load. This is
; env-derived config, NOT session state, so the unless(boundp(...)) guard
; that protects VCSessionId/VCSessionBase does NOT apply here. Without the
; unconditional reassignment, reloads would preserve a stale value bound by
; an earlier load (the original bug — banner kept showing 2222 after fallback
; changed to 22). Falls back to 22 for host network mode.
; cadence-rocky runs in host network mode so container port 22 = host port 22;
; bridge-mode + port-mapping users can override by setting VC_SSH_PORT in their
; Virtuoso CIW env before loading ramic_bridge.il.
VCsshPort = let((envPort)
    envPort = getShellEnvVar("VC_SSH_PORT")
    if(envPort then
        atoi(envPort)
    else
        22
    )
)

procedure(VCSendCallback(msg)
  ; Write result to a temp file pair instead of ipcWriteProcess — works around
  ; the IC23.1/RHEL8 bug where ipcWriteProcess stops firing after the first call.
  ; Daemon polls for the .done marker, reads data file, then deletes both.
  let((cbPort dataFile doneFile port)
    cbPort = VCPort + 1
    dataFile = sprintf(nil "/tmp/.ramic_cb_%d" cbPort)
    doneFile = sprintf(nil "/tmp/.ramic_cb_%d.done" cbPort)
    port = outfile(dataFile "w")
    when(port fprintf(port "%s" msg) close(port))
    port = outfile(doneFile "w")
    when(port close(port))
  )
)

procedure(VCMarkEvalStart()
  ; P0-1: record that an evaluation was ENTERED.
  ; The daemon gives every evaluation a deadline. When it expires the daemon
  ; declares the channel dead and stops delivering new evaluations, because
  ; this handler runs evalstring() synchronously inside the IPC callback and
  ; nothing outside the SKILL interpreter can interrupt it.
  ; This marker is how the daemon tells "the evaluation never started" from
  ; "the evaluation started and never returned". The daemon deletes it before
  ; it delivers the next request, so a marker found at deadline time always
  ; belongs to the request that timed out.
  let((startedFile port)
    startedFile = sprintf(nil "/tmp/.ramic_cb_%d.started" VCPort + 1)
    port = outfile(startedFile "w")
    when(port fprintf(port "%s" getCurrentTime()) close(port))
  )
)

procedure(VCIpcDataHandler(ipcId data)
  ; IPC data handler: eval SKILL expression, send STX (success) or NAK (error) via callback file.
  ; evalstring() bypasses CIW's interactive loop — printf output is line-buffered
  ; and won't appear until a "\n" is received or hiFlush() is called explicitly.
  ; We call hiFlush() after every evalstring to force CIW UI refresh.
  ;
  ; A hung evaluation (one that never returns) blocks this handler, and with
  ; it the whole SKILL interpreter — the CIW stops answering too. It cannot be
  ; cancelled from the outside: the daemon's answer to a timeout is to stop
  ; writing to this pipe and to report that the session must be restarted.
  let((result resultStr)
    when(VCEcho printf("[RAMIC Bridge (%L)] receive:%L\n" ipcId data))
    VCMarkEvalStart()
    if(errset(result=evalstring(strcat("(progn " data ")"))) then
        resultStr = sprintf(nil "%c%L%c" intToChar(2) result intToChar(30))
        when(VCEcho printf("[RAMIC Bridge (%L)] return:%L\n" ipcId result))
    else
        resultStr = sprintf(nil "%c%L%c" intToChar(21) errset.errset intToChar(30))
    )
    VCSendCallback(resultStr)
    hiFlush()  ; force CIW UI refresh — evalstring is line-buffered unlike interactive CIW
  )
)

procedure(VCIpcErrHandler(ipcId data)
    ; Parse VERSION:x.x.x and PORT:XXXXX lines emitted by daemon on startup.
    ; VERSION arrives first so it is stored before the banner fires on PORT.
    rexCompile("VERSION:\\([^ \n]*\\)")
    when(rexExecute(data)
        VCDVersion = rexSubstitute("\\1")
    )
    rexCompile("PORT:\\([0-9][0-9]*\\)")
    when(rexExecute(data)
        let((actualPort)
            actualPort = atoi(rexSubstitute("\\1"))
            when(actualPort > 0
                VCPort = actualPort
                VCSessionId = sprintf(nil "%s-%d" VCSessionBase VCPort)
                VCWriteSession(VCSessionId VCPort)
                printf("[RAMIC Bridge] Session '%s' on port %d\n" VCSessionId VCPort)
                ; Print banner here so the port is always known-correct.
                when(VCShowBanner
                    VCShowBanner = nil
                    VCPrintBanner()
                )
            )
        )
    )
)

procedure(VCIpcFinishHandler(ipcId data)
  ; Handler called when the Python daemon process exits
  let((exitStatus)
	exitStatus = ipcGetExitStatus(ipcId)
    printf("[RAMIC Bridge (%L)] exit at (%s) with state = %L\n" ipcId getCurrentTime() exitStatus)
  )
)

procedure(VCStart()
    ; Start the RAMIC Bridge daemon process.
    ; Passes port=0 so the OS assigns a free port automatically — supports multiple
    ; Virtuoso instances on the same host without port conflicts.
    ; The daemon prints "PORT:XXXXX" to stderr; VCIpcErrHandler captures it and
    ; writes the session registration file.
	if(boundp('VCIpc) && ipcIsAliveProcess(VCIpc) then
		printf("[RAMIC Bridge (%L)] is already running\n", VCIpc)
		printf("  bound port   : %d\n" VCPort)
		printf("  session id   : %s\n" VCSessionId)
		printf("  To switch profile/port, run in CIW:\n")
		printf("    VCStop()\n")
		printf("    load(\".../ramic_bridge.il\")\n")
		printf("  If that does not clear it, use VCStopAll() before loading again.\n")
	else
		prog((host logpath hostname username)
			; Determine host binding based on VCLocal setting
			if(VCLocal then
				host = "127.0.0.1"  ; Local-only connections
			else
				host = "0.0.0.0"   ; Accept connections from any IP
			)
			; Set log path if logging is enabled
			if(VCDLog then
				logpath = "/tmp/RB.log"
			else
				logpath = ""
			)
			; Session ID is finalized by VCIpcErrHandler once the OS-assigned port is known,
			; guaranteeing uniqueness when multiple Virtuoso instances run concurrently.
			hostname = getShellEnvVar("HOSTNAME")
			unless(hostname hostname = "localhost")
			hostname = car(parseString(hostname "."))  ; strip domain
			username = getShellEnvVar("USER")
			unless(username username = "user")
			VCSessionBase = sprintf(nil "%s-%s" hostname username)
			; Start the daemon with port=0 (OS assigns free port).
			; VCIpcErrHandler will parse PORT:N from stderr and write session file.
			VCIpc = ipcBeginProcess(sprintf(nil "env VC_SSH_PORT=%d VCLI_CAPABILITY=admin %s %s 0" VCsshPort VCDPath host) "" 'VCIpcDataHandler 'VCIpcErrHandler 'VCIpcFinishHandler logpath)
		)
		printf("[RAMIC Bridge (%L)] start at (%s)\n" VCIpc getCurrentTime())
	)
)

procedure(VCStop()
    ; Stop the RAMIC Bridge daemon process and remove session registration file.
	if(boundp('VCIpc) && ipcIsAliveProcess(VCIpc) then
		ipcKillProcess(VCIpc)
		VCDeleteSession(VCSessionId)
		VCPort = 0   ; clear stale port so next start shows correct value
	else
		printf("[RAMIC Bridge] is already down\n" )
	)
)

procedure(VCStopAll()
    ; Emergency function to kill all RAMIC Bridge daemon processes
    ; 1. Try graceful SIGTERM first, then force SIGKILL after 2s
    ; 2. Also kill any process holding the configured VCPort
    ; 3. Reset Skill-side IPC state
    ; 4. Remove this session's registration file
    sh(sprintf(nil "pgrep -f 'virtuoso-daemon' | xargs -r kill 2>/dev/null; sleep 2; pgrep -f 'virtuoso-daemon' | xargs -r kill -9 2>/dev/null; fuser -k %d/tcp 2>/dev/null" VCPort))
    VCDeleteSession(VCSessionId)
    VCIpc = 'unbound
    printf("[RAMIC Bridge] VCStopAll: all daemon processes killed, port %d released\n" VCPort)
)

procedure(VCWriteSession(sessionId port)
    ; Write session registration file using sh() to avoid SKILL fprintf buffering issues.
    ; File: ~/.cache/virtuoso_bridge/sessions/<session_id>.json
    let((dir filename hostname username)
        when(sessionId != ""
            hostname = getShellEnvVar("HOSTNAME")
            unless(hostname hostname = "localhost")
            username = getShellEnvVar("USER")
            unless(username username = "user")
            dir = strcat(getShellEnvVar("HOME") "/.cache/virtuoso_bridge/sessions")
            filename = strcat(dir "/" sessionId ".json")
            ; Use printf to write JSON (avoids sh() output-capture limitation)
            sh(sprintf(nil "mkdir -p \"%s\"" dir))
            sh(sprintf(nil "printf '{\"id\":\"%s\",\"port\":%d,\"pid\":0,\"host\":\"%s\",\"user\":\"%s\",\"created\":\"%s\"}' > \"%s\""
                sessionId port hostname username getCurrentTime() filename))
        )
    )
)

procedure(VCDeleteSession(sessionId)
    ; Remove session registration file on bridge stop.
    let((filename)
        when(sessionId != ""
            filename = strcat(getShellEnvVar("HOME") "/.cache/virtuoso_bridge/sessions/" sessionId ".json")
            when(isFile(filename)
                deleteFile(filename)
            )
        )
    )
)

; ============================================================================
; GUI Components for the RAMIC Bridge Monitor (once per session)
; ============================================================================
; Re-running hiCreateAppForm / hiInsertBannerMenu on reload causes form/menu
; conflicts; build GUI and banner menu only the first time.
unless(boundp('VCMonInstalled)
progn(
; Status label showing current bridge state
VCMState = hiCreateLabel(
	?name 		'VCMState
	?labelText 	"RAMIC Bridge State: ?"
	?justification CDS_JUSTIFY_LEFT
)

; Refresh button to update status display
VCMBtnRefresh = hiCreateButton(
	?name		'VCMBtnRefresh
	?buttonText	"Refresh"
	?callback	"VCMRefresh()"
)

; Start bridge button
VCMBtnStart = hiCreateButton(
	?name		'VCMBtnStart
	?buttonText	"Start"
	?callback	"VCStart() VCMRefresh()"
)

; Stop bridge button
VCMBtnStop = hiCreateButton(
	?name		'VCMBtnStop
	?buttonText	"Stop"
	?callback	"VCStop() VCMRefresh()"
)

; Port number input field
VCMIntPort = hiCreateIntField(
	?name		'VCMIntPort
	?prompt		"Port"
	?value		VCPort
	?defValue	VCPort
	?callback	nil
)

; Local-only connection checkbox
VCMBolLocal = hiCreateBooleanButton(
	?name		'VCMBolLocal
	?buttonText	"Local connect only"
	?value		VCLocal
	?defValue	VCLocal
	?callback	nil
)

; Echo bridging messages checkbox
VCMBolEcho = hiCreateBooleanButton(
	?name		'VCMBolEcho
	?buttonText	"Echo bridging string"
	?value		VCEcho
	?defValue	VCEcho
	?callback	nil
)

; Enable daemon logging checkbox
VCMBolLog = hiCreateBooleanButton(
	?name		'VCMBolLog
	?buttonText	"Daemon log (/tmp/RB.log)"
	?value		VCDLog
	?defValue	VCDLog
	?callback	nil
)

; Emergency kill all daemons button
VCMBtnStopAll = hiCreateButton(
	?name		'VCMBtnStopAll
	?buttonText	"KILL ALL DAEMON (USE AS BACKUP ONLY)"
	?callback	"VCStopAll() VCMRefresh()"
)

; Create the main monitor form
hiCreateAppForm(
	?name 'VCMonitor
	?formTitle "RAMIC Bridge Monitor"
	?fields list(
		list(VCMState		20:10	300:30)    ; Status label
		list(VCMBtnRefresh 	20:50 	100:30)    ; Refresh button
		list(VCMBtnStart  	150:50 	100:30)    ; Start button
		list(VCMBtnStop  	280:50 	100:30)    ; Stop button
		list(VCMIntPort		20:90	170:30 	30)  ; Port input
		list(VCMBolLocal	20:130	170:30)     ; Local-only checkbox
		list(VCMBolEcho		20:170	170:30)     ; Echo checkbox
		list(VCMBolLog		20:210	170:30)     ; Log checkbox
		list(VCMBtnStopAll 	20:250 	360:30)     ; Emergency stop button
	)
	?buttonLayout	'OKCancelApply
	?help			""
	?initialSize		400:320
	?minSize			400:320
	?maxSize			400:320
	?mapCB			"VCMRefresh()"
	?callback 		"VCMApply()"
)

; ============================================================================
; Menu Integration
; ============================================================================

; Create RAMIC menu in Virtuoso's banner
ramicMenu = hiCreatePulldownMenu(
	'ramicMenu
	"RAMIC"
	list(
		hiCreateMenuItem(
			?name 'RAMIC_Bridge
			?itemText "RAMIC Bridge..."
			?callback "hiDisplayForm(VCMonitor)"
		)
		hiCreateMenuItem(
			?name 'RAMIC_PrintLines
			?itemText "Print Empty Lines"
			?callback "VCPrintEmptyLines()"
		)
	)
)

; Insert the RAMIC menu into Virtuoso's main window
hiInsertBannerMenu(window(1) ramicMenu 3)
VCMonInstalled = t
)
) ; end unless(boundp 'VCMonInstalled) + progn

procedure(VCMRefresh()
    ; Refresh the monitor display with current status and settings
	when(boundp('VCMonInstalled)
	if(boundp('VCIpc) && ipcIsAliveProcess(VCIpc) then
		VCMonitor->VCMState->value = sprintf(nil "RAMIC Bridge State: Running | Session: %s | Port: %d" VCSessionId VCPort)
	else
		VCMonitor->VCMState->value = "RAMIC Bridge State: Down"
	)
	VCMonitor->VCMIntPort->value = VCPort
	VCMonitor->VCMBolLocal->value = VCLocal
	VCMonitor->VCMBolEcho->value = VCEcho
	VCMonitor->VCMBolLog->value = VCDLog
	)
)

procedure(VCMApply()
    ; Apply changes from the monitor form to the configuration
	when(boundp('VCMonInstalled)
	VCEcho = VCMonitor->VCMBolEcho->value
	prog((refresh)
	unless(VCMonitor->VCMBolLocal->value == VCLocal
		VCLocal = VCMonitor->VCMBolLocal->value
		refresh = t
	)
	unless(VCMonitor->VCMIntPort->value == VCPort
		VCPort = VCMonitor->VCMIntPort->value
		refresh = t
	)
	unless(VCMonitor->VCMBolLog->value == VCDLog
		VCDLog = VCMonitor->VCMBolLog->value
		refresh = t
	)
	when(refresh
		VCStop()
		VCStart()
	)
	)
	)
)

procedure(VCPrintEmptyLines()
	printf("\n\n\n\n\n\n\n\n\n\n\n\n")
)

; ============================================================================
; VCPrintBanner — print the Ready box to CIW (called by VCIpcErrHandler
;                 after PORT is confirmed, so the port is always correct)
; ============================================================================

procedure(VCPrintBanner()
    let((home daemonDisp ver)
        home = getShellEnvVar("HOME")
        ; Replace $HOME prefix with ~ for a compact path display
        daemonDisp = VCDPath
        when(strncmp(daemonDisp home strlen(home)) == 0
            daemonDisp = strcat("~" substring(daemonDisp strlen(home) + 1))
        )
        ver = if(VCDVersion != "" then VCDVersion else "?")
        printf("\n")
        printf("┌─────────────────────────────────────────┐\n")
        printf("│  vcli (Virtuoso CLI Bridge) — Ready     │\n")
        printf("├─────────────────────────────────────────┤\n")
        printf("│  Session : %-29s │\n" VCSessionId)
        printf("│  Port    : %-29d │\n" VCPort)
        printf("│  SSH     : %-29d │\n" VCsshPort)
        printf("│  Version : %-29s │\n" ver)
        printf("│  Daemon  : %-29s │\n" daemonDisp)
        printf("├─────────────────────────────────────────┤\n")
        printf("│  Terminal: vcli skill exec 'version()'  │\n")
        printf("│  Sessions: vcli session list            │\n")
        printf("└─────────────────────────────────────────┘\n")
        printf("\n")
    )
)

; ============================================================================
; vcli() — convenience wrapper: start bridge + show Ready banner.
; Banner prints after PORT is received so the port number is always correct.
; ============================================================================

procedure(vcli()
    if(boundp('VCIpc) && ipcIsAliveProcess(VCIpc) && VCPort > 0 then
        ; Already running — print banner immediately (port already known).
        VCPrintBanner()
    else
        ; Starting fresh — set flag so VCIpcErrHandler prints banner on PORT arrival.
        VCShowBanner = t
        VCStart()
        unless(boundp('VCIpc) && ipcIsAliveProcess(VCIpc)
            printf("[vcli] Bridge failed to start. Check VCDPath=%s\n" VCDPath)
            VCShowBanner = nil
        )
    )
)

; ============================================================================
; Full Virtuoso session: auto-load packages needed for streamout/schematic/ADE.
; Only loads packages that are present in the current Virtuoso installation.
; Skips silently if a package is not available (e.g., minimal CIW environment).
; Reload-safe: uses VCLoadedPackages guard so packages are not re-loaded on
; subsequent load() calls (only the first load in a given Virtuoso session
; triggers the load; reloads are no-ops).
; ============================================================================

; ============================================================================
; Package Loading — DISABLED
; 
; Virtuoso loads contexts (schematic/si/streamout) AUTOMATICALLY when needed.
; Manual loadi() calls are:
;   1. UNNECESSARY — contexts auto-load
;   2. POTENTIALLY HARMFUL — double-loading can cause parser state corruption
;   3. CAUSE OF "too many arguments" cascading errors in IC25
;
; Original code tried to preload contexts via loadi() for .ile/.aux files.
; This was for IC23 where explicit loading was needed for some operations.
; In IC25, Virtuoso handles all context loading internally.
; ============================================================================

VCLoadedPackages = nil

; procedure(VCLoadPackages()
;     ; ... (disabled — see above)
; )

; ============================================================================
; Auto-start on every load(): stop stale daemon, re-resolve daemon path via the
; search chain (VCResolveDaemonPath), start fresh.
; Wrapped in a procedure so no intermediate return values echo in CIW.
; ============================================================================

procedure(_VCAutoStart()
    VCPython = ""
    VCDPath = VCResolveDaemonPath()
    ; No manual package loading needed — Virtuoso handles context loading internally.
    ; Manual loadi() calls caused "too many arguments" parser errors in IC25.
    when(boundp('VCIpc) && ipcIsAliveProcess(VCIpc) VCStop())
    vcli()
)
_VCAutoStart()
