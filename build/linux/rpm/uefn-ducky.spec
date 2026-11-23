# The .rpm from the one-dir Linux build; build_rpm.sh passes ducky_version, ducky_tar
# (the dist/uefn-ducky.tar from build_exes.py) and ducky_packaging (packaging/linux).
# The bundle is PyInstaller output: no debuginfo, no stripping, no auto-generated
# requires from its private libraries.
%global debug_package %{nil}
%global __os_install_post %{nil}
%global _build_id_links none

Name:           uefn-ducky
Version:        %{ducky_version}
Release:        1
Summary:        AI assistant for Unreal Editor for Fortnite
License:        LicenseRef-Ducky-Source-Available
URL:            https://uefnducky.org
ExclusiveArch:  x86_64 aarch64
AutoReqProv:    no
Requires:       webkit2gtk4.1, libayatana-appindicator-gtk3, libsecret, gtk3, espeak-ng, portaudio
Recommends:     git
Recommends:     (wl-clipboard or xclip)

%description
UEFN Ducky runs AI agents, Verse tools and the UEFN editor bridge from one desktop app.

%prep
%setup -q -c -T
tar -xf %{ducky_tar}

%install
mkdir -p %{buildroot}/opt %{buildroot}%{_bindir} %{buildroot}%{_datadir}
cp -a uefn-ducky %{buildroot}/opt/uefn-ducky
ln -s /opt/uefn-ducky/uefn-ducky %{buildroot}%{_bindir}/uefn-ducky
install -Dm644 %{ducky_packaging}/uefn-ducky.desktop %{buildroot}%{_datadir}/applications/uefn-ducky.desktop
cp -r %{ducky_packaging}/icons %{buildroot}%{_datadir}/icons

%files
/opt/uefn-ducky
%{_bindir}/uefn-ducky
%{_datadir}/applications/uefn-ducky.desktop
%{_datadir}/icons/hicolor/*/apps/uefn-ducky.png
