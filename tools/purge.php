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

/*
 * Remove what Wildcard Aliases leaves in config.xml: the firewall aliases it created and its own
 * settings section. Run as root on the firewall by tools/install-dev.sh --uninstall. It only needs
 * OPNsense core, so it also works after the plugin files are gone.
 *
 * Prints one line per alias: "removed <name>" or "kept <name>" (still used by rules or other aliases).
 */

use OPNsense\Core\Config;
use OPNsense\Firewall\Alias;

/**
 * @return array ['removed' => [names], 'kept' => [name => [references]]]
 */
function wildcardaliases_purge()
{
    $config = Config::getInstance();
    $config->lock();
    $root = $config->object();

    // names configured in the plugin; "objects/object" is the layout before the rename
    $names = [];
    if (isset($root->OPNsense->WildcardAliases)) {
        foreach (['aliases' => 'alias', 'objects' => 'object'] as $list => $item) {
            foreach ($root->OPNsense->WildcardAliases->$list->$item ?? [] as $node) {
                $names[] = (string)$node->name;
            }
        }
    }

    $model = new Alias();
    $result = ['removed' => [], 'kept' => []];
    $delete = [];
    foreach ($model->aliases->alias->iterateItems() as $uuid => $alias) {
        $name = (string)$alias->name;
        // only external aliases, and only those we configured or described as ours
        $ours = in_array($name, $names) || strpos((string)$alias->description, 'Wildcard Aliases: ') !== false;
        if ((string)$alias->type != 'external' || !$ours) {
            continue;
        }
        $uses = $model->whereUsed($name);
        if (!empty($uses)) {
            $result['kept'][$name] = array_keys($uses);
        } else {
            $delete[$uuid] = $name;
        }
    }
    foreach ($delete as $uuid => $name) {
        $model->aliases->alias->del($uuid);
        $result['removed'][] = $name;
    }
    if (!empty($delete)) {
        $model->serializeToConfig(false, true);
    }

    $had_section = isset($root->OPNsense->WildcardAliases);
    unset($root->OPNsense->WildcardAliases);
    if (!empty($delete) || $had_section) {
        $config->save();
    } else {
        $config->unlock();
    }
    return $result;
}

if (PHP_SAPI == 'cli' && realpath($_SERVER['argv'][0] ?? '') === __FILE__) {
    require_once '/usr/local/opnsense/mvc/script/load_phalcon.php';
    $result = wildcardaliases_purge();
    foreach ($result['removed'] as $name) {
        echo "removed {$name}\n";
    }
    foreach ($result['kept'] as $name => $uses) {
        echo "kept {$name} (in use: " . implode(', ', $uses) . ")\n";
    }
}
