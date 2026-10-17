<?php

/*
 * Runs the plugin's MVC classes against a checkout of opnsense/core, outside of OPNsense.
 *
 * OPNSENSE_CORE points to the core checkout (default: .cache/opnsense-core, see "make php-test").
 * Every run uses a fresh, temporary config directory.
 */

$core = getenv('OPNSENSE_CORE') ?: __DIR__ . '/../../.cache/opnsense-core';
$mvc = realpath($core . '/src/opnsense/mvc');
if ($mvc === false) {
    fwrite(STDERR, "OPNsense core not found in {$core}, set OPNSENSE_CORE or use 'make php-test'\n");
    exit(1);
}
$plugin = realpath(__DIR__ . '/../../net/wildcard-aliases/src/opnsense/mvc/app');

$configDir = sys_get_temp_dir() . '/wildcardaliases-phpunit-' . getmypid();
@mkdir($configDir, 0700, true);
define('WILDCARDALIASES_TEST_CONFIGDIR', $configDir);
register_shutdown_function(function () use ($configDir) {
    exec('rm -rf ' . escapeshellarg($configDir));
});

require_once $mvc . '/app/config/AppConfig.php';
$config = new OPNsense\Core\AppConfig([
    'application' => [
        'controllersDir' => $mvc . '/app/controllers/',
        'modelsDir' => $mvc . '/app/models/',
        'viewsDir' => $mvc . '/app/views/',
        'libraryDir' => $mvc . '/app/library/',
        'contribDir' => realpath($core) . '/contrib',
        'configDefault' => '/does/not/exist/config.xml',
        'configDir' => $configDir,
        'cacheDir' => $configDir,
        'tempDir' => $configDir,
    ],
    'globals' => [
        'debug' => false,
        'owner' => '',
        'simulate_mode' => false,
    ],
]);

require_once $mvc . '/app/library/OPNsense/Autoload/Loader.php';
(new OPNsense\Autoload\Loader([
    $mvc . '/app/controllers/',
    $mvc . '/app/models/',
    $mvc . '/app/library/',
    $plugin . '/controllers/',
    $plugin . '/models/',
]))->register();
