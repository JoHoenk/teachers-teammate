# PSScriptAnalyzer settings for //:ps_script_analyzer (aspect_rules_lint). Errors and warnings
# fail the lint; every rule that is switched off is listed with the reason.
@{
    Severity     = @('Error', 'Warning')

    ExcludeRules = @(
        # The two scripts are run by CI and write their progress to the job log; Write-Host is
        # the point. (The rule's concern is output a caller wants to capture or redirect.)
        'PSAvoidUsingWriteHost',

        # The installer test's helpers are named after what they check (Assert-Shortcut,
        # Remove-Install). They are not cmdlets anybody imports, and changing a state is what an
        # installer test does; -WhatIf on them would mean nothing.
        'PSUseSingularNouns',
        'PSUseShouldProcessForStateChangingFunctions'
    )
}
