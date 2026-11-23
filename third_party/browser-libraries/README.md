# Browser libraries

What the pages of RouterFleet Improved load, all served by the installation
itself - nothing comes from another host. The files are committed under
`static_files/`; `package.json` and `package-lock.json` beside this file list
the same versions so that Trivy can check them for known vulnerabilities. They
are not used to install anything.

| Library | Version | Licence | Served from |
|---|---|---|---|
| admin-lte | 4.10.2 | MIT | `static_files/vendor/adminlte-4.10.2/` (its stylesheet includes Bootstrap's) |
| bootstrap | 5.3.9 | MIT | `static_files/vendor/bootstrap-5.3.9/` |
| @popperjs/core | 2.11.8 | MIT | inside `bootstrap.bundle.min.js` |
| datatables.net | 3.1.5 | MIT | `static_files/vendor/datatables-3.1.5/` |
| datatables.net-bs5 | 3.1.5 | MIT | `static_files/vendor/datatables-3.1.5/` |
| datatables.net-responsive | 4.1.1 | MIT | `static_files/vendor/datatables-3.1.5/` |
| datatables.net-responsive-bs5 | 4.1.1 | MIT | `static_files/vendor/datatables-3.1.5/` |
| tom-select | 2.7.0 | Apache-2.0 | `static_files/vendor/tom-select-2.7.0/` (the searchable selects) |
| overlayscrollbars | 2.17.1 | MIT | `static_files/vendor/overlayscrollbars-2.17.1/` (the sidebar) |
| @fortawesome/fontawesome-free | 7.3.1 | (CC-BY-4.0 AND OFL-1.1 AND MIT) | `static_files/vendor/fontawesome-7.3.1/` |
| diff2html | 3.4.56 | MIT | `static_files/plugins/diff2html/` |
| chart.js | 4.6.1 | MIT | `static_files/vendor/chart.js-4.6.1/` |
| @xterm/xterm | 6.0.0 | MIT | `static_files/vendor/xterm-6.0.0/` (the device console) |
| @xterm/addon-fit | 0.11.0 | MIT | `static_files/vendor/xterm-6.0.0/addon-fit.js` |
| Source Sans Pro (font) | 3.006R | OFL-1.1 | `static_files/fonts/source-sans-pro/` |

Notes:
- Each file keeps the licence header it was published with; where a library
  ships a separate licence file, it sits beside the library.
- Every file under `static_files/vendor/` is the file of the npm tarball, whose
  published SHA-512 was checked when it was added. Files without a version
  header of their own (the DataTables Bootstrap 5 integration, Tom Select's
  stylesheet, diff2html, xterm.js) are identified by their digest in the test.
- Font Awesome Free: icons CC BY 4.0, fonts SIL OFL 1.1, code MIT - the
  attribution is in the header of `all.min.css`.
- No jQuery: since 0.24 every page script is plain JavaScript.

When a library is changed, update this table, `package.json` and
`package-lock.json` together; a test fails if the versions in the files and
here disagree.
