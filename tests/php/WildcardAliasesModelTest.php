<?php

namespace tests\OPNsense\WildcardAliases;

use OPNsense\Core\Config;
use OPNsense\WildcardAliases\WildcardAliases;
use OPNsense\Firewall\Alias;
use PHPUnit\Framework\TestCase;

require_once __DIR__ . '/../../tools/purge.php';

class WildcardAliasesModelTest extends TestCase
{
    protected function setUp(): void
    {
        // start every test from an empty configuration
        file_put_contents(
            WILDCARDALIASES_TEST_CONFIGDIR . '/config.xml',
            "<?xml version=\"1.0\"?>\n<opnsense><system><hostname>test</hostname></system>" .
            "<interfaces><lan><if>em0</if><ipaddr>192.0.2.1</ipaddr><subnet>24</subnet></lan></interfaces></opnsense>\n"
        );
        Config::getInstance()->forceReload();
        // core caches UniqueConstraint results per validation counter, which restarts for every model
        // instance. Fine within one API request, but in a test process it leaks between tests.
        (new \ReflectionProperty(\OPNsense\Base\Constraints\UniqueConstraint::class, 'itemmap'))->setValue(null, []);
    }

    private static function messages($model)
    {
        $result = [];
        foreach ($model->performValidation(true) as $message) {
            $result[$message->getField()][] = $message->getMessage();
        }
        return $result;
    }

    private static function validModel()
    {
        $model = new WildcardAliases();
        $model->general->enabled = '1';
        $model->general->upstreams = '127.0.0.1:5353';
        return $model;
    }

    private static function addWildcard($model, $name, $patterns, $families = 'IPv4,IPv6')
    {
        $wildcard = $model->aliases->alias->Add();
        $wildcard->name = $name;
        $wildcard->patterns = $patterns;
        $wildcard->families = $families;
        return $wildcard;
    }

    public function testDefaults()
    {
        $model = new WildcardAliases();
        $this->assertEquals('0', (string)$model->general->enabled);
        $this->assertEquals('127.0.0.1:5354', (string)$model->general->listen);
        $this->assertEquals('300', (string)$model->general->min_hold);
        $this->assertEquals('closed', (string)$model->general->fail_mode);
    }

    public function testValidConfiguration()
    {
        $model = self::validModel();
        self::addWildcard($model, 'azure_storage', '*.blob.core.windows.net,login.microsoftonline.com');
        $this->assertEquals([], self::messages($model));
    }

    public function testUpstreamIsRequired()
    {
        $model = new WildcardAliases();
        $this->assertArrayHasKey('general.upstreams', self::messages($model));
    }

    public function testInvalidAddresses()
    {
        $model = self::validModel();
        $model->general->listen = '127.0.0.1:99999';
        $model->general->upstreams = 'dns.google';
        $messages = self::messages($model);
        $this->assertArrayHasKey('general.listen', $messages);
        $this->assertArrayHasKey('general.upstreams', $messages);
    }

    public function testIpv6Addresses()
    {
        $model = self::validModel();
        $model->general->listen = '[::1]:5354,127.0.0.1:5354';
        $model->general->upstreams = '[2001:db8::53]:53,::1';
        $this->assertEquals([], self::messages($model));
    }

    public function testUpstreamLoopIsRejected()
    {
        $model = self::validModel();
        $model->general->upstreams = '127.0.0.1:5354';
        $this->assertArrayHasKey('general.upstreams', self::messages($model));
    }

    public function testHoldTimes()
    {
        $model = self::validModel();
        $model->general->min_hold = '7200';
        $model->general->max_hold = '3600';
        $this->assertArrayHasKey('general.max_hold', self::messages($model));
    }

    public function testWildcardValidation()
    {
        $model = self::validModel();
        $bad_name = self::addWildcard($model, 'not-valid', '*.example.com');
        $bad_pattern = self::addWildcard($model, 'bad_pattern', 'a.*.example.com');
        self::addWildcard($model, 'dup', 'a.example.com');
        $dup = self::addWildcard($model, 'dup', 'b.example.com');
        $messages = self::messages($model);
        $this->assertArrayHasKey($bad_name->name->__reference, $messages);
        $this->assertArrayHasKey($bad_pattern->patterns->__reference, $messages);
        $this->assertArrayHasKey($dup->name->__reference, $messages);
    }

    public function testExistingAliasConflict()
    {
        $aliases = new Alias();
        $alias = $aliases->aliases->alias->Add();
        $alias->name = 'webservers';
        $alias->type = 'host';
        $alias->content = '192.0.2.10';
        $aliases->serializeToConfig();
        Config::getInstance()->save();

        $model = self::validModel();
        $wildcard = self::addWildcard($model, 'webservers', '*.example.com');
        $this->assertArrayHasKey($wildcard->name->__reference, self::messages($model));
    }

    public function testSyncAliasesCreatesAndUpdates()
    {
        $model = self::validModel();
        self::addWildcard($model, 'azure_storage', '*.blob.core.windows.net', 'IPv4');
        $disabled = self::addWildcard($model, 'disabled_alias', '*.example.com');
        $disabled->enabled = '0';
        $model->serializeToConfig();

        $this->assertTrue($model->syncAliases());
        $alias = (new Alias())->getByName('azure_storage', true);
        $this->assertNotNull($alias);
        $this->assertEquals('external', (string)$alias->type);
        $this->assertEquals('IPv4', (string)$alias->proto);
        $this->assertEquals('Wildcard Aliases: *.blob.core.windows.net', (string)$alias->description);
        $this->assertNull((new Alias())->getByName('disabled_alias', true), 'disabled wildcard aliases get no firewall alias');

        // nothing to do the second time
        $this->assertFalse((new WildcardAliases())->syncAliases());

        // changed families/patterns are carried over
        $model = new WildcardAliases();
        foreach ($model->aliases->alias->iterateItems() as $wildcard) {
            if ((string)$wildcard->name == 'azure_storage') {
                $wildcard->families = 'IPv4,IPv6';
                $wildcard->patterns = '*.blob.core.windows.net,*.queue.core.windows.net';
            }
        }
        $model->serializeToConfig();
        $this->assertTrue($model->syncAliases());
        $alias = (new Alias())->getByName('azure_storage', true);
        $this->assertEquals('IPv4,IPv6', (string)$alias->proto);
        $this->assertStringContainsString('*.queue.core.windows.net', (string)$alias->description);
    }

    public function testSyncAliasesNeverTakesOverForeignAliases()
    {
        $aliases = new Alias();
        $alias = $aliases->aliases->alias->Add();
        $alias->name = 'webservers';
        $alias->type = 'host';
        $alias->content = '192.0.2.10';
        $aliases->serializeToConfig();
        Config::getInstance()->save();

        $model = self::validModel();
        self::addWildcard($model, 'webservers', '*.example.com');
        $this->assertFalse($model->syncAliases());
        $alias = (new Alias())->getByName('webservers', true);
        $this->assertEquals('host', (string)$alias->type);
        $this->assertEquals('192.0.2.10', (string)$alias->content);
    }

    public function testConfigRoundTrip()
    {
        $model = self::validModel();
        self::addWildcard($model, 'azure_storage', '*.blob.core.windows.net');
        $model->serializeToConfig();
        Config::getInstance()->save();
        Config::getInstance()->forceReload();

        $reloaded = new WildcardAliases();
        $names = [];
        foreach ($reloaded->aliases->alias->iterateItems() as $wildcard) {
            $names[] = (string)$wildcard->name;
        }
        $this->assertEquals(['azure_storage'], $names);
        $this->assertEquals('127.0.0.1:5353', (string)$reloaded->general->upstreams);
        // write the generated config.xml for the template round trip test (tests/test_template_core.py)
        if (getenv('WILDCARDALIASES_EXPORT_CONFIG')) {
            copy(WILDCARDALIASES_TEST_CONFIGDIR . '/config.xml', getenv('WILDCARDALIASES_EXPORT_CONFIG'));
        }
    }

    private static function addFirewallAlias($name, $type, $content = '', $description = '')
    {
        $aliases = new Alias();
        $alias = $aliases->aliases->alias->Add();
        $alias->name = $name;
        $alias->type = $type;
        $alias->content = $content;
        $alias->description = $description;
        $aliases->serializeToConfig(false, true);
        Config::getInstance()->save();
    }

    public function testPurgeRemovesOwnAliasesAndSettings()
    {
        $model = self::validModel();
        self::addWildcard($model, 'unused_wc', '*.example.com');
        self::addWildcard($model, 'used_wc', '*.example.org');
        $model->serializeToConfig();
        $model->syncAliases();
        // used_wc is referenced by another alias, so it must survive; foreign aliases are never touched
        self::addFirewallAlias('group', 'network', 'used_wc');
        self::addFirewallAlias('other_external', 'external');
        self::addFirewallAlias('other_host', 'host', '192.0.2.10', 'Wildcard Aliases: lookalike');

        $result = wildcardaliases_purge();
        $this->assertEquals(['unused_wc'], $result['removed']);
        $this->assertEquals(['used_wc'], array_keys($result['kept']));

        Config::getInstance()->forceReload();
        $this->assertFalse(isset(Config::getInstance()->object()->OPNsense->WildcardAliases));
        $aliases = new Alias();
        $this->assertNull($aliases->getByName('unused_wc', true));
        foreach (['used_wc', 'group', 'other_external', 'other_host'] as $name) {
            $this->assertNotNull($aliases->getByName($name, true), $name);
        }
        // nothing left to do
        $again = wildcardaliases_purge();
        $this->assertEquals([], $again['removed']);
        $this->assertEquals(['used_wc'], array_keys($again['kept']));
    }

    public function testPurgeHandlesLayoutBeforeRename()
    {
        self::addFirewallAlias('old_wc', 'external');
        $root = Config::getInstance()->object();
        $root->OPNsense->addChild('WildcardAliases')->addChild('objects')->addChild('object')->addChild('name', 'old_wc');
        Config::getInstance()->save();

        $this->assertEquals(['removed' => ['old_wc'], 'kept' => []], wildcardaliases_purge());
        Config::getInstance()->forceReload();
        $this->assertFalse(isset(Config::getInstance()->object()->OPNsense->WildcardAliases));
    }

    public function testControllersResolve()
    {
        // the parent classes and their abstract contracts must exist in core
        foreach ([
            \OPNsense\WildcardAliases\Api\SettingsController::class,
            \OPNsense\WildcardAliases\Api\ServiceController::class,
            \OPNsense\WildcardAliases\IndexController::class,
        ] as $class) {
            $reflection = new \ReflectionClass($class);
            $this->assertFalse($reflection->isAbstract(), $class);
        }
        $service = new \ReflectionClass(\OPNsense\WildcardAliases\Api\ServiceController::class);
        $this->assertEquals('wildcardaliases', $service->getStaticPropertyValue('internalServiceName'));
    }
}
