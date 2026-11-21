# Browser libraries

What the pages of RouterFleet Improved load, all served by the installation
itself - nothing comes from another host. The files are committed under
`static_files/`; `package.json` and `package-lock.json` beside this file list
the same versions so that Trivy can check them for known vulnerabilities. They
are not used to install anything.

| Library | Version | Licence | Served from |
|---|---|---|---|
| admin-lte | 3.2.0 | MIT | `static_files/AdminLTE-3.2.0/dist/` |
| jquery | 3.7.1 | MIT | `static_files/AdminLTE-3.2.0/plugins/jquery/` |
| bootstrap | 4.6.2 | MIT | `static_files/AdminLTE-3.2.0/plugins/bootstrap/` |
| popper.js | 1.16.1 | MIT | `inside bootstrap.bundle.min.js` |
| datatables.net | 1.11.4 | MIT | `static_files/AdminLTE-3.2.0/plugins/datatables/` |
| datatables.net-bs4 | 1.11.4 | MIT | `static_files/AdminLTE-3.2.0/plugins/datatables-bs4/` |
| datatables.net-responsive | 2.2.9 | MIT | `static_files/AdminLTE-3.2.0/plugins/datatables-responsive/` |
| datatables.net-responsive-bs4 | 2.2.9 | MIT | `static_files/AdminLTE-3.2.0/plugins/datatables-responsive/` |
| overlayscrollbars | 1.13.0 | MIT | `static_files/AdminLTE-3.2.0/plugins/overlayScrollbars/` |
| @fortawesome/fontawesome-free | 5.15.4 | (CC-BY-4.0 AND OFL-1.1 AND MIT) | `static_files/AdminLTE-3.2.0/plugins/fontawesome-free/` |
| chosen-js | 1.8.7 | MIT | `static_files/plugins/chosen/` |
| diff2html | 3.4.56 | MIT | `static_files/plugins/diff2html/` |
| chart.js | 4.6.1 | MIT | `static_files/vendor/chart.js-4.6.1/` |
| Source Sans Pro (font) | 3.006R | OFL-1.1 | `static_files/fonts/source-sans-pro/` |

Notes:
- Each file keeps the licence header it was published with; where a library
  ships a separate licence file, it sits beside the library.
- The DataTables Bootstrap 4 and Responsive integration files carry no version
  of their own. AdminLTE ships them as matched sets with the core libraries,
  so they are listed with the core versions.
- Font Awesome Free: icons CC BY 4.0, fonts SIL OFL 1.1, code MIT - the
  attribution is in the header of `all.min.css`.

Known issue, recorded rather than hidden: **Bootstrap 4 is end-of-life.**
CVE-2024-6531 is an XSS in its carousel component with no 4.x release fixing it.
No RouterFleet page uses the carousel, so it is not reachable here; Trivy's
database does not list it for this package, which is why the scan is clean.
Moving to Bootstrap 5 means moving to AdminLTE 4.

When a library is changed, update this table, `package.json` and
`package-lock.json` together; a test fails if the versions in the files and
here disagree.
