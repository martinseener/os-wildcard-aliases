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

namespace OPNsense\WildcardAliases;

use OPNsense\Base\BaseModel;
use OPNsense\Base\Messages\Message;
use OPNsense\Core\Config;
use OPNsense\Firewall\Alias;

class WildcardAliases extends BaseModel
{
    /**
     * parse "ip", "ip:port" or "[ipv6]:port", returns [host, port] or null when invalid
     */
    public static function parseAddress($value)
    {
        $value = trim($value);
        $host = $value;
        $port = '53';
        if (preg_match('/^\[([0-9a-fA-F:\.]+)\](?::([0-9]+))?$/', $value, $matches)) {
            $host = $matches[1];
            $port = $matches[2] ?? '53';
        } elseif (substr_count($value, ':') == 1) {
            list($host, $port) = explode(':', $value);
        }
        if (!filter_var($host, FILTER_VALIDATE_IP) || !ctype_digit($port) || $port < 1 || $port > 65535) {
            return null;
        }
        return [$host, (int)$port];
    }

    /**
     * {@inheritdoc}
     */
    public function performValidation($validateFullModel = false)
    {
        $messages = parent::performValidation($validateFullModel);

        // cheap enough to always check both, the upstream loop check depends on the listen addresses
        $listen = [];
        foreach (['listen', 'upstreams'] as $field) {
            $node = $this->general->$field;
            foreach (array_filter(explode(',', (string)$node)) as $item) {
                $address = self::parseAddress($item);
                if ($address === null) {
                    $messages->appendMessage(new Message(
                        sprintf(gettext('"%s" is not a valid IP address with optional port.'), $item),
                        $node->__reference
                    ));
                } elseif ($field == 'listen') {
                    $listen[] = $address;
                } elseif (in_array($address, $listen)) {
                    $messages->appendMessage(new Message(
                        gettext('An upstream must not point to a listen address of Wildcard Aliases itself.'),
                        $node->__reference
                    ));
                }
            }
        }

        if ((int)(string)$this->general->max_hold < (int)(string)$this->general->min_hold) {
            $messages->appendMessage(new Message(
                gettext('The maximum hold time must not be lower than the minimum hold time.'),
                $this->general->max_hold->__reference
            ));
        }

        $fwAliases = new Alias();
        foreach ($this->aliases->alias->iterateItems() as $wildcard) {
            if (!$validateFullModel && !$wildcard->isFieldChanged()) {
                continue;
            }
            $fwAlias = $fwAliases->getByName((string)$wildcard->name);
            if ($fwAlias != null && (string)$fwAlias->type != 'external') {
                $messages->appendMessage(new Message(
                    gettext('An alias with this name already exists and is not managed by Wildcard Aliases.'),
                    $wildcard->name->__reference
                ));
            }
        }

        return $messages;
    }

    /**
     * create or update the "external" aliases backing the enabled wildcard aliases
     * @return bool true when the alias configuration changed
     */
    public function syncAliases()
    {
        $fwAliases = new Alias();
        $changed = false;
        foreach ($this->aliases->alias->iterateItems() as $wildcard) {
            if ((string)$wildcard->enabled != '1') {
                continue;
            }
            $name = (string)$wildcard->name;
            $description = sprintf('Wildcard Aliases: %s', str_replace(',', ', ', (string)$wildcard->patterns));
            if (!empty((string)$wildcard->description)) {
                $description = sprintf('%s (%s)', (string)$wildcard->description, $description);
            }
            $fwAlias = $fwAliases->getByName($name);
            if ($fwAlias == null) {
                $fwAlias = $fwAliases->aliases->alias->Add();
                $fwAlias->name = $name;
                $fwAlias->type = 'external';
                $changed = true;
            } elseif ((string)$fwAlias->type != 'external') {
                // never take over an alias the user created for something else
                syslog(LOG_WARNING, sprintf('wildcardaliases: alias %s exists and is not of type external, skipped', $name));
                continue;
            }
            if ((string)$fwAlias->description != $description) {
                $fwAlias->description = $description;
                $changed = true;
            }
            if ((string)$fwAlias->proto != (string)$wildcard->families) {
                $fwAlias->proto = (string)$wildcard->families;
                $changed = true;
            }
        }
        if ($changed) {
            $fwAliases->serializeToConfig();
            Config::getInstance()->save();
        }
        return $changed;
    }
}
