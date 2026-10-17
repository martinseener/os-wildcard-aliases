# Developer helpers. The plugin itself is built by the opnsense/plugins framework,
# see net/wildcard-aliases/Makefile and README.md.

VENV?=		.venv
PYTHON?=	${VENV}/bin/python
SCRIPTS=	net/wildcard-aliases/src/opnsense/scripts/OPNsense/WildcardAliases
# PHP tests run against opnsense/core with the PHP + Phalcon versions OPNsense uses, on macOS:
#   brew tap shivammathur/php && brew tap shivammathur/extensions
#   brew install shivammathur/php/php@8.4 shivammathur/extensions/phalcon5@8.4 phpunit
PHP84?=		/opt/homebrew/opt/php@8.4/bin/php
PHPUNIT?=	$$(command -v phpunit)
CORE?=		.cache/opnsense-core
CORE_BRANCH?=	master

all: test

${VENV}:
	python3 -m venv ${VENV}
	${VENV}/bin/pip install -q dnspython jinja2 pytest pytest-asyncio

test: ${VENV}
	${PYTHON} -m pytest -q

${CORE}:
	git clone -q --depth 1 --branch ${CORE_BRANCH} https://github.com/opnsense/core.git ${CORE}

# model/controller tests against opnsense/core, then render the PHP-written config.xml with core's
# real template engine and validate it with the daemon's parser
php-test: ${VENV} ${CORE}
	OPNSENSE_CORE=${CORE} WILDCARDALIASES_EXPORT_CONFIG=$(CURDIR)/.cache/config.xml \
	    ${PHP84} ${PHPUNIT} -c tests/php/phpunit.xml
	OPNSENSE_CORE=${CORE} WILDCARDALIASES_CONFIG_XML=$(CURDIR)/.cache/config.xml \
	    ${PYTHON} -m pytest -q tests/test_template_core.py

lint: ${VENV}
	${VENV}/bin/pip install -q flake8
	${VENV}/bin/flake8 --max-line-length=120 net tests
	for f in $$(find net tests/php tools -name '*.php' -o -name '*.inc'); do ${PHP84} -l $$f >/dev/null || exit 1; done

check: lint test php-test

# run the daemon locally without touching pf, e.g. make run CONFIG=/tmp/wildcardaliases.json
run: ${VENV}
	${PYTHON} ${SCRIPTS}/wildcardaliasesd.py -f --dry-run -c ${CONFIG}

# copy the plugin onto a (test!) firewall, e.g. make install-dev HOST=root@fw.example.com
install-dev:
	./tools/install-dev.sh ${HOST}

# remove it again, including its settings, the aliases it created (unless still in use) and the learned state
uninstall-dev:
	./tools/install-dev.sh --uninstall ${HOST}

.PHONY: all test php-test lint check run install-dev uninstall-dev
