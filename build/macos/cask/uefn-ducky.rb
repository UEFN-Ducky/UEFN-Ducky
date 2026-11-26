cask "uefn-ducky" do
  arch arm: "arm64", intel: "x64"

  version "0.0.0"
  sha256 arm:   "0000000000000000000000000000000000000000000000000000000000000000",
         intel: "0000000000000000000000000000000000000000000000000000000000000000"

  url "https://uefnducky.org/downloads/macos/UEFN-Ducky-#{version}-macos-#{arch}.dmg"
  name "UEFN Ducky"
  desc "AI toolkit for Unreal Editor for Fortnite"
  homepage "https://uefnducky.org/"

  depends_on macos: ">= :ventura"

  app "UEFN Ducky.app"

  caveats <<~EOS
    UEFN Ducky is not notarized by Apple yet. The first time you open it, macOS blocks it:
    open System Settings > Privacy & Security and click "Open Anyway".
  EOS
end
