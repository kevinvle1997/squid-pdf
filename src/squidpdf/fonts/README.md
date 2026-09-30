# Fonts we ship

The faces squid-pdf draws with when a document's own font can't be used, and the
faces new text can be drawn in. `squidpdf.core.fonts.CATALOG` lists them; a test
checks every file here is in it and is the face it says.

Most are under the SIL Open Font License 1.1; Latin Modern is under the GUST
Font License (the LaTeX Project Public License) and FreeSans and FreeSerif under
the GPL-3 with its font exception. All three let a PDF embed the font without
the document taking on the licence. Each family's licence is in `licenses/`.
Fetched 2026-09-26; the families from Poppins down, 2026-09-29.

## Where each came from

| Family | Version | Why | Source | Licence |
| --- | --- | --- | --- | --- |
| Liberation Sans, Serif, Mono | 2.1.5 | Same letter widths as Arial/Helvetica, Times New Roman/Times, Courier New/Courier | Ubuntu 24.04 `fonts-liberation` 1:2.1.5-3 (archive.ubuntu.com, .deb sha256 `065c2ab1abc9108b17d401016dc594b79750904390f095845c93bb06e1153acc`), built from github.com/liberationfonts/liberation-fonts | `licenses/Liberation.txt` (from that repo) |
| Carlito | 1.104 | Same widths as Calibri | github.com/google/fonts `ofl/carlito` (upstream googlefonts/carlito @ 3a810ca) | `licenses/Carlito.txt` |
| Caladea | 1.001 | Same widths as Cambria | github.com/google/fonts `ofl/caladea` (upstream googlefonts/caladea @ 336a529) | `licenses/Caladea.txt` |
| Noto Sans, Noto Serif | 2.015 | The broadest: Latin, Greek, Cyrillic. Draws a line whose letters its look-alike lacks | github.com/notofonts/notofonts.github.io `fonts/Noto{Sans,Serif}/unhinted/ttf` | `licenses/Noto.txt` |
| Poppins | 4.004 | The typeface itself, for documents that use it | github.com/google/fonts `ofl/poppins` (upstream itfoundry/Poppins @ 738d9d6) | `licenses/Poppins.txt` |
| Open Sans | 3.003 | The typeface itself | github.com/googlefonts/opensans @ bd7e376, `fonts/ttf` (the static files) | `licenses/OpenSans.txt` |
| Montserrat | 9.000 | The typeface itself | github.com/JulietaUla/Montserrat @ 555facf, `fonts/ttf` (the static files) | `licenses/Montserrat.txt` |
| Nunito | 3.602 | The typeface itself | github.com/google/fonts `ofl/nunito` (upstream googlefonts/nunito @ 8c6a9bb) | `licenses/Nunito.txt` |
| PT Sans, PT Serif | 2.003W, 1.000W | The typeface itself | github.com/google/fonts `ofl/ptsans`, `ofl/ptserif` (`PT_Sans-Web-*.ttf`, renamed to our file names) | `licenses/PTSans.txt`, `licenses/PTSerif.txt` |
| FreeSans, FreeSerif | 20120503 | The typeface itself: GNU FreeFont, which LibreOffice and many invoice generators embed | ftp.gnu.org/gnu/freefont/freefont-ttf-20120503.zip | `licenses/FreeFont.txt` (the font exception, then the GPL-3) |
| Latin Modern Roman 10, 12, 17, Roman Caps 10, Mono 10 | 2.004 | Same widths as LaTeX's Computer Modern (CMR10, CMBX10, CMTT10...), checked against pdfTeX output | gust.org.pl `lm2.004otf.zip` | `licenses/LatinModern.txt` |
| Inter | 4.001 | Sans, for new text | github.com/google/fonts `ofl/inter` (upstream rsms/inter @ 66647c0) | `licenses/Inter.txt` |
| Roboto | 3.015 | Sans, for new text | github.com/google/fonts `ofl/roboto` (upstream googlefonts/roboto-classic @ 91d5d3e) | `licenses/Roboto.txt` |
| Lato | 2.015 | Sans, for new text | github.com/google/fonts `ofl/lato` (upstream googlefonts/LatoGFVersion @ 080cb69) | `licenses/Lato.txt` |
| EB Garamond | 1.003 | Serif, for new text | github.com/google/fonts `ofl/ebgaramond` (upstream octaviopardo/EBGaramond12 @ 106a4a6) | `licenses/EBGaramond.txt` |
| IBM Plex Serif | 2.6 | Serif, for new text | github.com/google/fonts `ofl/ibmplexserif` (upstream googlefonts/plex @ 43279c1) | `licenses/IBMPlex.txt` |
| IBM Plex Mono | 2.3 | Fixed width, for new text | github.com/google/fonts `ofl/ibmplexmono` (upstream googlefonts/plex @ 9ab3b5b) | `licenses/IBMPlex.txt` |
| Caveat | 2.000 | Handwriting, for signatures | github.com/google/fonts `ofl/caveat` (upstream googlefonts/caveat @ 59745e8) | `licenses/Caveat.txt` |
| Great Vibes | 1.103 | A script signature face | github.com/google/fonts `ofl/greatvibes` (upstream googlefonts/great-vibes @ f95eb96) | `licenses/GreatVibes.txt` |

Every file is as its source ships it, except the five families below.

## Made from variable fonts

Inter, Roboto, EB Garamond, Caveat and Nunito ship only as variable fonts (one file for
every weight). MuPDF draws a variable font at its default weight only, so each
style here is a fixed instance cut with fontTools 4.65
(`fontTools.varLib.instancer.instantiateVariableFont`), at weight 400 (Regular,
Italic) or 700 (Bold, Bold Italic), from the upright or the italic file. Other
axes are pinned: Inter's optical size at 14 (text), Roboto's width at 100. The
name table was then set to plain four-style names (family, style, full name,
PostScript name) and the STAT table dropped. None of these four families has a
Reserved Font Name, so the OFL allows this under the same names.

Families that do reserve their name (Merriweather, Source Code Pro, Dancing
Script, Raleway, Playfair Display) were left out rather than cut this way; families with a reserved name
here (Lato, Carlito, IBM Plex, Liberation) ship unmodified.

| Source file | sha256 |
| --- | --- |
| `Inter[opsz,wght].ttf` | `29160a80ff49ddcab2c97711247e08b1fab27a484a329ce8b813d820dc559031` |
| `Inter-Italic[opsz,wght].ttf` | `acd98e64795781b2058f07b18475e0ecee2a0fe2b42a49e2f9e37d0d6bf66ce6` |
| `Roboto[wdth,wght].ttf` | `d7598e12c5dbef095ff8272cfc55da0250bd07fbdecbac8a530b9b277872a134` |
| `Roboto-Italic[wdth,wght].ttf` | `9725a847af6b460ffca162ae66d20dad48b01876137947180b42d7dcd7887182` |
| `EBGaramond[wght].ttf` | `ef9512f92f6d579e5dc75af59a5a4b1b8b47d2eda89e00b954d44520e5369027` |
| `EBGaramond-Italic[wght].ttf` | `bba2c4499c93c9612b90b9825d32b07da52fce2fe57562a1eb6b833553f93c4e` |
| `Caveat[wght].ttf` | `0bdb6b660482d31531b3945849fba5916b3ef8695da7024a9e6b9ee3c4157988` |
| `Nunito[wght].ttf` | `bb55a5ca5c2042335b3991af27c4d0705d0ef41cac6164ac737fd8f2a1e85207` |
| `Nunito-Italic[wght].ttf` | `b520cc871868b0acfca1beda875df7f4a44ebce914f8a89f83977fc9c09529c8` |

## Left out

- **Liberation Sans Narrow** (Arial Narrow's look-alike): its only release is
  1.07, under GPL-2 with a font exception, not the OFL. Arial Narrow is drawn in
  Liberation Sans instead, and the app says the widths may differ.
- **Raleway, Playfair Display**: variable fonts only, with a Reserved Font Name
  (see above). A document in either is drawn in a plain face of its kind.
- **FreeSans matched to Liberation Sans**: tried first, but most of FreeSans'
  letters are other widths, so FreeSans ships as itself.

## Checksums of the files here

| File | sha256 |
| --- | --- |
| `Caladea-Bold.ttf` | `ae3cb2dcbc925809dd29d2a44e9802211cab66be541bacbfc9c08c74b27c3742` |
| `Caladea-BoldItalic.ttf` | `ccabaa7b7e2fdf253d2b1a5fa699dd8a3df8d835a9eb285ad82631a677eb76c0` |
| `Caladea-Italic.ttf` | `4359a8e24f748b6447b1ff6d7a174febe70961d29f8bb8634b56dacd740a3deb` |
| `Caladea-Regular.ttf` | `f1e899278b7b4491aba5b6a8253c4b04c050cc59b21865be5c37559a775153cd` |
| `Carlito-Bold.ttf` | `bb5d20f79b82599ec72983597437373a80f2d2085fa91fc144fd74e876a594db` |
| `Carlito-BoldItalic.ttf` | `b32928186c119599e03ca6a1ffc680fdcb7fac95772f4b95d989cf6cd3861517` |
| `Carlito-Italic.ttf` | `0b019225e58d702bfedcbd35c21696769f8ee115cb6343f84c2f240312450d1c` |
| `Carlito-Regular.ttf` | `f6418f708baede9789daef5d458c0f53d2a888af9820e8062934e504fedc6595` |
| `Caveat-Bold.ttf` | `4b8c072d166b59e41450aee6a765cece70e0ebb1044610b1711134c1dc7659fa` |
| `Caveat-Regular.ttf` | `7d43f9a761711182d42b5d08fccd3f3604ffcca4f3ba045249ed3138a5cdcd78` |
| `EBGaramond-Bold.ttf` | `7a1e0d58cf171a8537e776a769c9735d94d2f203952f1c53076984515689fa00` |
| `EBGaramond-BoldItalic.ttf` | `e18de2cf42a6a75fa9f13f7bde8836e900ed74edef7d599aae09f64f8a2be330` |
| `EBGaramond-Italic.ttf` | `1c6103150c31d57535b5a478bfaff306b9b35a70cc2e9e738f0acd997c0dde33` |
| `EBGaramond-Regular.ttf` | `fb924d382de190911961eadcee4002a630dee5ffa97fb5ecf6f5a74d854addc7` |
| `FreeSans.ttf` | `c80858440d8fb618e0ac5ff6f16251dbfa6b3316f00f3cdd17d477297dd87b04` |
| `FreeSansBold.ttf` | `982534a3731416a15e2756601721f26053f68bf4239011550f3dd23ce6308215` |
| `FreeSansBoldOblique.ttf` | `deb48d677df0b521793df8da28729316bfcc255309aeb6dacfbd38241ac9b7ba` |
| `FreeSansOblique.ttf` | `43ef481308dfb3aa1103a75699cb4976139b6339eda0901df69a25533f07cf6b` |
| `FreeSerif.ttf` | `658563c732eafdc851de2b3f38b27d0afcde5674b2f93440971b9693438c6494` |
| `FreeSerifBold.ttf` | `973791e2bb5ef4ba7ac9e7423f8a8d8441468da3a37c7e38ab38256011f5f261` |
| `FreeSerifBoldItalic.ttf` | `38e39596c9daec677633761324216ffda969c65b9515cf273b001521f22cb2b3` |
| `FreeSerifItalic.ttf` | `1cef05c1ea5d88f0e06b212bdb0510edf65c5456523dd81ddccfe9847a77284f` |
| `GreatVibes-Regular.ttf` | `8d509802186f1b51572531ecf313e8098f9a5bfdfaca93f0c9b34467f9982d15` |
| `IBMPlexMono-Bold.ttf` | `ac27abd6450a64dd94467580a02fe6235156d5b92f2926ebbc8e7489df64e0be` |
| `IBMPlexMono-BoldItalic.ttf` | `af4e05a761e98c1adf064c48a6352c9bec1a6ad70982cd2a544149323391f98e` |
| `IBMPlexMono-Italic.ttf` | `3362fc791b0652193328b862c1c5f23a789bc7288b1617fa63302f88689a2a34` |
| `IBMPlexMono-Regular.ttf` | `6a3412f058c7d8dfd9170c41e85ade48e5156ecb89356110ca57a0a27734af46` |
| `IBMPlexSerif-Bold.ttf` | `534c02c295999dd86e770457ece1d43db0de9256dd98bf741426f63ae904209e` |
| `IBMPlexSerif-BoldItalic.ttf` | `2a32b76ac19c1942bf5942dbbd2a1566e5f1ae9833e421ebcf36d3522715e153` |
| `IBMPlexSerif-Italic.ttf` | `4b75b38be4673527231f49c48818d090c913d5042dd5c747b525bf6185d29ecb` |
| `IBMPlexSerif-Regular.ttf` | `e882efa9c41949a528ac2369079ec5ef050c1c996bbd0bacce3c3326d44cf80d` |
| `Inter-Bold.ttf` | `4415d4bc58b90623d886f5dadb6b3ec477ee142cf1f4f9b9c0d48fe93c081924` |
| `Inter-BoldItalic.ttf` | `dec877d273c891b3e0480a41de1b0e283416e51857e7166a91f7c719f676e079` |
| `Inter-Italic.ttf` | `9e94e7f93f3da8d622cf0d82c5f6feaeca6f38e3cbc955a9e58d69340aa3a9a9` |
| `Inter-Regular.ttf` | `4962a8564b2ab085f902f68bb5f33d07a5bc98bcd0f0ae7d1bf8303aa467cdbb` |
| `Lato-Bold.ttf` | `8a0aace75d33794eece4b28187bfc1df0bbd2888b5d8a56e01788c8d65d16be1` |
| `Lato-BoldItalic.ttf` | `62c1b7f0d2e74b45960154c3520efc337b553db0961bfdc950d5618334596cc8` |
| `Lato-Italic.ttf` | `e399c44efe1387100531d26c7e4800c5d12251b890d6654a3098c7c679cb1786` |
| `Lato-Regular.ttf` | `d636e4683231f931eda222d588e944d082bfd3bdba02f928bee461c0f185b251` |
| `LiberationMono-Bold.ttf` | `626655e94dd82f3f42549daf995c921b0915fa8ab1f4b839559e8892ea41d240` |
| `LiberationMono-BoldItalic.ttf` | `15eb161953e3ecc7fc05a3fec8a59e0f4e0a54a4e375736adf8e98d65981f813` |
| `LiberationMono-Italic.ttf` | `a71b2c25c89da05cf0e7c4dbba8d473fdead0b181ae56165217747d2c1f39215` |
| `LiberationMono-Regular.ttf` | `395fa5ab8d40c8eba390ced528744ea75a7f69aabf3e68b6f925ca0e39a27370` |
| `LiberationSans-Bold.ttf` | `3973aa5054fb467dd5627245d3dc82e37bf16fe075756156a570455871351582` |
| `LiberationSans-BoldItalic.ttf` | `c80fa7f2ffa0e01d4d8dcd6a6d1e43eda665222d1b4db597dde1174c456006cf` |
| `LiberationSans-Italic.ttf` | `830c5fa600505fb4c1a271b4271c53c44bae43f492b2a240d0e98a3a7a380121` |
| `LiberationSans-Regular.ttf` | `4659bc0c58c5028dd488ec928d41d9265db43d9b669fc14ca8b0832daca7b144` |
| `LiberationSerif-Bold.ttf` | `9e66c25e20868756bf12eb24a520a4552727c9cd108f577f7ae33e3c0110e39c` |
| `LiberationSerif-BoldItalic.ttf` | `379010e87421a883f7bbfa7936d23dfe0257a54ce850819b74556e9f2c615a8a` |
| `LiberationSerif-Italic.ttf` | `eaeaf7f2b12544ecde64290c1fa7ec403ff70fd58b313637b265400b20249242` |
| `LiberationSerif-Regular.ttf` | `705903ae1382f0150f115d6f10a30f5b2f3d6ade649c474afe6366c39174fb65` |
| `lmmono10-italic.otf` | `14e6328a5383d2b927c47051093ce0aff60deea07e6df71b8b99ee8d9c2ff8f2` |
| `lmmono10-regular.otf` | `22deb6d3be3ffcb40b33f0e010afb256c677d97c2a2bdee1f5740cdc97751558` |
| `lmroman10-bold.otf` | `102fe06c430a8b681b2bf6876b7cd967ae4d47b4b6b41d915eb7913b726d9fb1` |
| `lmroman10-bolditalic.otf` | `c37a28eed7a6e03f792b98b5e5f637b2fcda378bb4855f99284f1a88fe35f124` |
| `lmroman10-italic.otf` | `c1fce25075567bb8dbf2151658c3b442690041db17a2d49fc9e55905ea5b7169` |
| `lmroman10-regular.otf` | `1aa18cfefa58132c52ce5de70db1fd1154201c19cd2b2cdaffba4906a33e6852` |
| `lmroman12-bold.otf` | `28c8782ac2b6486958b5dc7610ada7800c53546ff7f36bc65909a876e1cd338e` |
| `lmroman12-italic.otf` | `ada6b3f451238784e5a5ea75d43efa1eb4329cd75ce65bf047ed2f31b7b5e2f3` |
| `lmroman12-regular.otf` | `e6be218ae83e61aa8a29990d3cdc401c678c1962188cb9a4a8b6359e4f5e5870` |
| `lmroman17-regular.otf` | `ed048c80fb4b67663b80899b89fe5fba17d7442e1286d61fd08fe5689c6b7c3c` |
| `lmromancaps10-regular.otf` | `1ab40332a969892c7ed6cb010193b5276ccef1da6798bcbd1a465fee23d29334` |
| `Montserrat-Bold.ttf` | `bc6e854971cea46b463be6f9eef4d9cd52f51cfc1fc0dd90c9d3e6483dc0ec61` |
| `Montserrat-BoldItalic.ttf` | `b4c121b337aaa977d711b6f397c5b9672d720af622555f1e3f9d3b87f2983665` |
| `Montserrat-Italic.ttf` | `b8fa0ea7f433669d94d27e66ea81cc627deb71861ed9d20f53d7afa1f33ba86a` |
| `Montserrat-Regular.ttf` | `3e8abe50c44c82e2242e97d1ec8c0d385c4890cdc50447bcdb8605c81a38cfb2` |
| `NotoSans-Bold.ttf` | `87cb2d84472a7d66da659ee47b6cdb9552326e8c128245231f191b6ac72529d9` |
| `NotoSans-BoldItalic.ttf` | `3d367743f371f28671d2764e911a53d7c20ec9b6aa8791d059e7090389fc52a5` |
| `NotoSans-Italic.ttf` | `678288f868807d4d64a6f3b51466871d117d915780381ce9d0ed4b3bcbd06d37` |
| `NotoSans-Regular.ttf` | `f3961a9cde016d41a4879aecda1474d3a36d6bf54fa0e4643de029cc2248b0e8` |
| `NotoSerif-Bold.ttf` | `24ad531e6b05ddad8c3d89572d2c93eb86a6b74e652ce7ee3c3e171de68e84c3` |
| `NotoSerif-BoldItalic.ttf` | `1bc4f86502eaa368718f6192bee022ea9a703d5af1c18b7d291212657b63074a` |
| `NotoSerif-Italic.ttf` | `c4b3c971741ecdb40f5a443bce754e8fe91efe761b6ea10c92be7c3597cdadc4` |
| `NotoSerif-Regular.ttf` | `a15cfbbc1539d707115111d672d590a3d70d4f74b4c0a315956da20ae19a14e1` |
| `Nunito-Bold.ttf` | `18614d7c88dfa5fb6d25eef3f4d159790d34cfd7eb84935496c503be9c87865d` |
| `Nunito-BoldItalic.ttf` | `e9a13913af94298dad9bac582d886ec209e042f9e52e1e4a3cafc06d3ad2d826` |
| `Nunito-Italic.ttf` | `89b106ca1ee7e0aed814d949305ba45104204c2ac4b59dfc6caf45a7e5879aaa` |
| `Nunito-Regular.ttf` | `c58d1037233693d124c6eec882bab831dad7a106ba9db0fe8c06032c8224e2e7` |
| `OpenSans-Bold.ttf` | `27da758f4dcac9a65abe914c13b463b42982b9909bc65713424099f4810bd1e6` |
| `OpenSans-BoldItalic.ttf` | `d672a770037104b6af45e1336b3d3c1729c8aea940f81e010f5a8a7319c29a21` |
| `OpenSans-Italic.ttf` | `93bc1bb6abf4e6b7c75d7131714061d5b57cc478abcabe4cb3519bb38fb917aa` |
| `OpenSans-Regular.ttf` | `c53aceea2dcf5b4098099c0c4d0a061d17e178a049317b42a422b1a9f7f8eb59` |
| `Poppins-Bold.ttf` | `983676516167748b74de6f4771fb384c664fd913acb8b471122ecacf5da5ea6c` |
| `Poppins-BoldItalic.ttf` | `3572ac8116a0ac7317d342262b29937bcbaf94d8f03f90df6fe666fa7e2fb43a` |
| `Poppins-Italic.ttf` | `4fa76ae75b40f926420514044722cb97f32186cafd3b38263cc34dad7174d46d` |
| `Poppins-Regular.ttf` | `7e65201e9b79159e2300267cc885e16c8dcef2424cdfa09a29bfb0980a94a7ba` |
| `PTSans-Bold.ttf` | `3128bd5ecf01816e59a23d54c57a7a6b14615b07db53ff277c77376010265b05` |
| `PTSans-BoldItalic.ttf` | `81ac221cdd02bccfa679c74adb122478e9d092e65a722e31ca11469961483785` |
| `PTSans-Italic.ttf` | `5a90fe2d0cd798700935240580bdcc12c0ffc9102c0c7163b3418e13bc21debd` |
| `PTSans-Regular.ttf` | `9cc831490532009bae2b3ce0d39c62adfc889060beb421593bfd9d2396d0f10a` |
| `PTSerif-Bold.ttf` | `038ba7336bd7ea14f12ad155bed51a4345cac5153275d521dec3ba04021c526e` |
| `PTSerif-BoldItalic.ttf` | `f003788ba08981eb0988b3557a6f224a53dab49c20e283e8b74d5af3c466f8be` |
| `PTSerif-Italic.ttf` | `f57e95ff9dc85691a3b2e193f2028db36f6663939a46c0fc4f286d618b80b7ce` |
| `PTSerif-Regular.ttf` | `a4951fade06ff8f09b7673aa81ffb65a8cd409e24d3289a6dc670bc4dda2557a` |
| `Roboto-Bold.ttf` | `60d8a1b2e78f863ab3b3b7193bc67df9719071adb2e5cd70573e658d96c35d95` |
| `Roboto-BoldItalic.ttf` | `fdb2fad89019d21f59ea789445d40ae0bc9c0e9af074a1e4b3caf8b9462e6749` |
| `Roboto-Italic.ttf` | `a13a74400cc0484d5c1170b35bdeb0135276344892bbfbf67758e7bebe78f4e5` |
| `Roboto-Regular.ttf` | `9f96fb98bceb05ce1ec28319db4e5eec19d54a1b62321b738afbab39460fc02f` |

## Changing them

A new or changed file changes what's drawn and what's judged, so bump
`LIBRARY_VERSION` in `core/constants.py`: every saved analysis is then worked
out again, and browsers fetch the font list again.
