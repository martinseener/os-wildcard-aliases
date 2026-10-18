{#
 # Copyright (C) 2026 Martin Seener <martin@sysorchestra.com>
 # All rights reserved.
 #
 # Redistribution and use in source and binary forms, with or without modification,
 # are permitted provided that the following conditions are met:
 #
 # 1. Redistributions of source code must retain the above copyright notice,
 #    this list of conditions and the following disclaimer.
 #
 # 2. Redistributions in binary form must reproduce the above copyright notice,
 #    this list of conditions and the following disclaimer in the documentation
 #    and/or other materials provided with the distribution.
 #
 # THIS SOFTWARE IS PROVIDED ``AS IS'' AND ANY EXPRESS OR IMPLIED WARRANTIES,
 # INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY
 # AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
 # AUTHOR BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY,
 # OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
 # SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
 # INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
 # CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
 # ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
 # POSSIBILITY OF SUCH DAMAGE.
 #}

<script>
    $(document).ready(function() {
        // the domain list only applies to "Allow for the listed domains only"
        function updatePrivateDomains() {
            const enabled = $('#wildcardaliases\\.general\\.private_addresses').val() === 'domains';
            const $field = $('#wildcardaliases\\.general\\.private_domains');
            $field.prop('disabled', !enabled);
            if ($field.hasClass('tokenize2_init_done')) {
                if (enabled) {
                    $field.tokenize2().enable();
                } else {
                    $field.tokenize2().disable();
                }
            }
            // "Clear All", "Copy" and "Text" next to the field (OPNsense shows "Paste" itself when needed)
            ['clear-options_', 'copy-options_', 'to-text_'].forEach(function (prefix) {
                $(document.getElementById(prefix + 'wildcardaliases.general.private_domains')).toggle(enabled);
            });
        }
        $('#wildcardaliases\\.general\\.private_addresses').on('change', updatePrivateDomains);

        mapDataToFormUI({'frm_general': '/api/wildcardaliases/settings/get'}).done(function() {
            formatTokenizersUI();
            $('.selectpicker').selectpicker('refresh');
            updatePrivateDomains();
            updateServiceControlUI('wildcardaliases');
        });

        $("#{{formGridAlias['table_id']}}").UIBootgrid({
            search: '/api/wildcardaliases/settings/search_alias/',
            get: '/api/wildcardaliases/settings/get_alias/',
            set: '/api/wildcardaliases/settings/set_alias/',
            add: '/api/wildcardaliases/settings/add_alias/',
            del: '/api/wildcardaliases/settings/del_alias/',
            toggle: '/api/wildcardaliases/settings/toggle_alias/'
        });

        // loading the dialog leaves its tokenizers in a "focused" state without real focus, which
        // hides their placeholder hint until the field is clicked once; reset that state
        $("#{{formGridAlias['edit_dialog_id']}}").on('shown.bs.modal', function () {
            $(this).find('select.tokenize').each(function () {
                $(this).tokenize2().trigger('tokenize:deselect');
            });
        });

        $("#reconfigureAct").SimpleActionButton({
            onPreAction: function() {
                const dfObj = $.Deferred();
                saveFormToEndpoint('/api/wildcardaliases/settings/set', 'frm_general', dfObj.resolve, true, dfObj.reject);
                return dfObj;
            },
            onAction: function(data, status) {
                updateServiceControlUI('wildcardaliases');
                loadStats();
            }
        });

        function loadStats() {
            ajaxGet('/api/wildcardaliases/service/stats', {}, function(data, status) {
                const $body = $('#stats_table > tbody').empty();
                const rows = (data && data.tables) ? data.tables : [];
                if (rows.length === 0) {
                    $body.append($('<tr>').append($('<td colspan="5">').text("{{ lang._('No data, the service is not running or nothing was learned yet.') }}")));
                }
                rows.forEach(function(row) {
                    const last = row.last_learned ? new Date(row.last_learned * 1000).toLocaleString() : '-';
                    $body.append($('<tr>').append(
                        $('<td>').append($('<a href="#">').text(row.name).on('click', function (e) {
                            e.preventDefault();
                            showEntriesOf(row.name);
                        })),
                        $('<td>').text(row.patterns),
                        $('<td>').text(row.entries + ' / ' + row.max_entries),
                        $('<td>').text(row.learned_total + (row.evicted_total ? ' (' + row.evicted_total + " {{ lang._('evicted') }})" : '')),
                        $('<td>').text(last)
                    ));
                });
                const stats = (data && data.stats) ? data.stats : {};
                $('#stats_summary').text(stats.queries !== undefined ?
                    "{{ lang._('Queries') }}: " + stats.queries + ', ' +
                    "{{ lang._('watched') }}: " + stats.matched + ', ' +
                    "{{ lang._('exact names resolved') }}: " + (stats.prefetched || 0) + ', ' +
                    "{{ lang._('upstream errors') }}: " + stats.upstream_errors + ', ' +
                    "{{ lang._('table errors') }}: " + stats.backend_errors + ', ' +
                    "{{ lang._('private addresses ignored') }}: " + (stats.private_ignored || 0) + ', ' +
                    "{{ lang._('refused (overload)') }}: " + (stats.refused_overload || 0) : '');
            });
        }

        $('a[data-toggle="tab"][href="#status"]').on('shown.bs.tab', loadStats);

        // learned addresses of one alias, with the domains they came from
        let entriesGrid = null;
        let entriesWanted = null;

        function formatDuration(seconds) {
            const d = Math.floor(seconds / 86400), h = Math.floor(seconds % 86400 / 3600);
            const m = Math.floor(seconds % 3600 / 60), s = seconds % 60;
            if (d > 0) return d + 'd ' + h + 'h';
            if (h > 0) return h + 'h ' + m + 'm';
            if (m > 0) return m + 'm ' + s + 's';
            return s + 's';
        }

        function reloadEntries() {
            if (entriesGrid === null) {
                entriesGrid = $('#grid-entries').UIBootgrid({
                    search: '/api/wildcardaliases/service/entries',
                    datakey: 'address',
                    options: {
                        selection: false,
                        multiSelect: false,
                        requestHandler: function (request) {
                            request['alias'] = $('#entries_alias').val() || '';
                            return request;
                        },
                        formatters: {
                            // domain names come from DNS queries: always render them as text
                            text: function (column, row) {
                                return $('<div>').text(row[column.id] || '').html();
                            },
                            duration: function (column, row) {
                                return formatDuration(parseInt(row[column.id], 10) || 0);
                            }
                        }
                    }
                });
                $('#entries_alias_wrapper').detach().insertBefore('#grid-entries-header .search');
            } else {
                $('#grid-entries').bootgrid('reload');
            }
        }

        function loadEntries() {
            ajaxGet('/api/wildcardaliases/service/stats', {}, function (data, status) {
                const $select = $('#entries_alias');
                const current = entriesWanted || $select.val();
                entriesWanted = null;
                $select.empty();
                ((data && data.tables) || []).forEach(function (row) {
                    // mark it as the default selection: bootstrap-select replaces a first option that
                    // was only selected via .val() by its "Select a wildcard alias" placeholder
                    $select.append($('<option>').val(row.name).text(row.name).attr('selected', row.name === current));
                });
                $select.selectpicker('refresh');
                reloadEntries();
            });
        }

        function showEntriesOf(name) {
            entriesWanted = name;
            $('a[data-toggle="tab"][href="#entries"]').tab('show');
        }

        $('a[data-toggle="tab"][href="#entries"]').on('shown.bs.tab', loadEntries);
        $('#entries_alias').on('changed.bs.select', reloadEntries);
        $('#refreshStats').click(loadStats);
    });
</script>

<ul class="nav nav-tabs" data-tabs="tabs" id="maintabs">
    <li class="active"><a data-toggle="tab" href="#aliases">{{ lang._('Aliases') }}</a></li>
    <li><a data-toggle="tab" href="#general">{{ lang._('Settings') }}</a></li>
    <li><a data-toggle="tab" href="#status">{{ lang._('Status') }}</a></li>
    <li><a data-toggle="tab" href="#entries">{{ lang._('Learned addresses') }}</a></li>
</ul>

<div class="tab-content content-box">
    <div id="aliases" class="tab-pane fade in active">
        {{ partial('layout_partials/base_bootgrid_table', formGridAlias) }}
    </div>
    <div id="general" class="tab-pane fade in">
        {{ partial('layout_partials/base_form', ['fields': generalForm, 'id': 'frm_general']) }}
    </div>
    <div id="status" class="tab-pane fade in">
        <div class="col-md-12">
            <p id="stats_summary"></p>
            <table class="table table-striped table-condensed" id="stats_table">
                <thead>
                    <tr>
                        <th>{{ lang._('Alias') }}</th>
                        <th>{{ lang._('Patterns') }}</th>
                        <th>{{ lang._('Entries') }}</th>
                        <th>{{ lang._('Learned') }}</th>
                        <th>{{ lang._('Last learned') }}</th>
                    </tr>
                </thead>
                <tbody></tbody>
            </table>
            <button class="btn btn-default" id="refreshStats" type="button">{{ lang._('Refresh') }}</button>
            <p class="text-muted">{{ lang._('Click an alias to see its learned addresses.') }}</p>
        </div>
    </div>
    <div id="entries" class="tab-pane fade in">
        <div id="entries_alias_wrapper" class="btn-group">
            <select id="entries_alias" class="selectpicker" data-live-search="true" data-width="250px"
                    title="{{ lang._('Select a wildcard alias') }}"></select>
        </div>
        <table id="grid-entries" class="table table-condensed table-hover table-striped table-responsive">
            <thead>
                <tr>
                    <th data-column-id="address" data-type="string" data-identifier="true" data-formatter="text">{{ lang._('Address') }}</th>
                    <th data-column-id="domains" data-type="string" data-formatter="text">{{ lang._('Learned from (most recent domains)') }}</th>
                    <th data-column-id="expires_in" data-type="numeric" data-formatter="duration">{{ lang._('Expires in') }}</th>
                </tr>
            </thead>
            <tbody></tbody>
        </table>
    </div>
</div>

{{ partial('layout_partials/base_apply_button', {'data_endpoint': '/api/wildcardaliases/service/reconfigure', 'data_service_widget': 'wildcardaliases'}) }}
{{ partial('layout_partials/base_dialog', ['fields': formDialogAlias, 'id': formGridAlias['edit_dialog_id'], 'label': lang._('Edit wildcard alias')]) }}
