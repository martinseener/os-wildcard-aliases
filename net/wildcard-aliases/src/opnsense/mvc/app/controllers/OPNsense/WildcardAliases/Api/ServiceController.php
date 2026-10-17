<?php

/*
 * Copyright (C) 2026 Martin Seener <martin@sysorchestra.com>
 * All rights reserved.
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions are met:
 *
 * 1. Redistributions of source code must retain the above copyright notice,
 *    this list of conditions and the following disclaimer.
 *
 * 2. Redistributions in binary form must reproduce the above copyright
 *    notice, this list of conditions and the following disclaimer in the
 *    documentation and/or other materials provided with the distribution.
 *
 * THIS SOFTWARE IS PROVIDED ``AS IS'' AND ANY EXPRESS OR IMPLIED WARRANTIES,
 * INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY
 * AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
 * AUTHOR BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY,
 * OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
 * SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
 * INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
 * CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
 * ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
 * POSSIBILITY OF SUCH DAMAGE.
 */

namespace OPNsense\WildcardAliases\Api;

use OPNsense\Base\ApiMutableServiceControllerBase;
use OPNsense\Core\Backend;

class ServiceController extends ApiMutableServiceControllerBase
{
    protected static $internalServiceClass = '\OPNsense\WildcardAliases\WildcardAliases';
    protected static $internalServiceTemplate = 'OPNsense/WildcardAliases';
    protected static $internalServiceEnabled = 'general.enabled';
    protected static $internalServiceName = 'wildcardaliases';

    private $aliasesChanged = false;

    /**
     * a configuration change only needs a SIGHUP, learned addresses survive it
     */
    protected function reconfigureForceRestart()
    {
        return false;
    }

    /**
     * new aliases must be loaded into the firewall before they can be referenced
     */
    protected function invokeFirewallReload()
    {
        return $this->aliasesChanged;
    }

    public function reconfigureAction()
    {
        if ($this->request->isPost() && $this->serviceEnabled()) {
            $this->aliasesChanged = $this->getModel()->syncAliases();
        }
        return parent::reconfigureAction();
    }

    /**
     * learned table statistics, as written by the daemon
     */
    public function statsAction()
    {
        $response = json_decode((new Backend())->configdRun('wildcardaliases stats'), true);
        if (!is_array($response) || !isset($response['tables'])) {
            return ['tables' => [], 'stats' => []];
        }
        $rows = [];
        foreach ($response['tables'] as $name => $table) {
            $table['name'] = $name;
            $table['patterns'] = implode(', ', $table['patterns'] ?? []);
            $rows[] = $table;
        }
        return ['tables' => $rows, 'stats' => $response['stats'] ?? []];
    }
}
