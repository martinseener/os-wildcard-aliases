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
        mapDataToFormUI({'frm_general': '/api/wildcardaliases/settings/get'}).done(function() {
            formatTokenizersUI();
            $('.selectpicker').selectpicker('refresh');
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
                        $('<td>').text(row.name),
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
                    "{{ lang._('upstream errors') }}: " + stats.upstream_errors + ', ' +
                    "{{ lang._('table errors') }}: " + stats.backend_errors : '');
            });
        }

        $('a[data-toggle="tab"][href="#status"]').on('shown.bs.tab', loadStats);
        $('#refreshStats').click(loadStats);
    });
</script>

<ul class="nav nav-tabs" data-tabs="tabs" id="maintabs">
    <li class="active"><a data-toggle="tab" href="#aliases">{{ lang._('Aliases') }}</a></li>
    <li><a data-toggle="tab" href="#general">{{ lang._('Settings') }}</a></li>
    <li><a data-toggle="tab" href="#status">{{ lang._('Status') }}</a></li>
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
            <p class="text-muted">{{ lang._('The addresses themselves can be inspected under Firewall: Diagnostics: Aliases.') }}</p>
        </div>
    </div>
</div>

{{ partial('layout_partials/base_apply_button', {'data_endpoint': '/api/wildcardaliases/service/reconfigure', 'data_service_widget': 'wildcardaliases'}) }}
{{ partial('layout_partials/base_dialog', ['fields': formDialogAlias, 'id': formGridAlias['edit_dialog_id'], 'label': lang._('Edit wildcard alias')]) }}
