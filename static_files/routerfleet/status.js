/* The status page: the backup and device charts, drawn for the range chosen
   above each one, in the colours of the light or dark mode. */
(function () {
    // The first range of each chart is the one shown first
var first = document.querySelector('.rf-range button');
var start = first ? first.dataset.days : 7;
var charts = {}, shown = {backups: start, devices: start};
    function chartTheme() {
        var dark = document.documentElement.classList.contains('rf-dark');
        Chart.defaults.color = dark ? '#c3ccd5' : '#666';
        Chart.defaults.borderColor = dark ? 'rgba(255, 255, 255, .10)' : 'rgba(0, 0, 0, .08)';
    }
    window.addEventListener('rf-theme-change', function () { chartTheme(); draw('backups', shown.backups); draw('devices', shown.devices); });
    var colors = {teal: '#14a89a', red: '#d64545', navy: '#1f3a5f', green: '#1f9d55', purple: '#7e57c2'};

    function draw(kind, days) {
        shown[kind] = days;
        chartTheme();
        var url = kind === 'backups' ? '/backup_statistics_data/' : '/router_status_data/';
        fetch(url + '?days=' + days, {credentials: 'same-origin'}).then(function (r) { return r.json(); }).then(function (data) {
            var canvas = document.getElementById(kind === 'backups' ? 'backupChart' : 'routerStatusChart');
            if (charts[kind]) { charts[kind].destroy(); }
            var config = kind === 'backups' ? {
                type: 'bar',
                data: {labels: data.dates, datasets: [
                    {label: 'Successful', data: data.success_data, backgroundColor: colors.teal, borderRadius: 4},
                    {label: 'Failed', data: data.error_data, backgroundColor: colors.red, borderRadius: 4}
                ]},
                options: {
                    maintainAspectRatio: false,
                    scales: {y: {beginAtZero: true, ticks: {precision: 0}}},
                    onClick: function (event, elements) {
                        if (!elements.length) { return; }
                        var element = elements[0];
                        var type = element.datasetIndex === 1 ? 'errors' : 'success';
                        window.location = '/backup/backup_list/?type=' + type + '&date=' + data.dates[element.index];
                    },
                    onHover: function (event, elements) {
                        event.native.target.style.cursor = elements.length ? 'pointer' : 'default';
                    }
                }
            } : {
                type: 'line',
                data: {labels: data.dates, datasets: [
                    {label: 'Online', data: data.online_data, borderColor: colors.green, backgroundColor: 'rgba(31, 157, 85, .12)', fill: true, tension: .25},
                    {label: 'Offline (enabled)', data: data.offline_data, borderColor: colors.red, backgroundColor: 'rgba(214, 69, 69, .10)', fill: true, tension: .25},
                    {label: 'Disabled', data: data.disabled_data, borderColor: colors.purple, backgroundColor: 'rgba(126, 87, 194, .10)', fill: true, tension: .25}
                ]},
                options: {
                    maintainAspectRatio: false,
                    interaction: {mode: 'nearest', intersect: false},
                    scales: {y: {beginAtZero: true, ticks: {precision: 0}}},
                    onClick: function (event, elements) {
                        if (!elements.length) { return; }
                        // The list shows the devices in that state now
                        window.location = '/router/list/?state=' + ['online', 'offline', 'disabled'][elements[0].datasetIndex];
                    },
                    onHover: function (event, elements) {
                        event.native.target.style.cursor = elements.length ? 'pointer' : 'default';
                    }
                }
            };
            charts[kind] = new Chart(canvas, config);
        });
    }

    document.querySelectorAll('.rf-range').forEach(function (group) {
        group.addEventListener('click', function (event) {
            var button = event.target.closest('button');
            if (!button) { return; }
            group.querySelectorAll('button').forEach(function (b) { b.classList.toggle('active', b === button); });
            draw(group.dataset.chart, button.dataset.days);
        });
    });
    draw('backups', start);
    draw('devices', start);
})();
