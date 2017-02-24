# flac2mp3

Recode and retag FLACs to MP3 for releasing at torrent tracker.

## Usage

    flac2mp3.py [-h] [--vbr|--cbr] [--new] path

Here CBR goes for LAME CBR 320 Kbps and VBR for LAME VBR V0.

Flag 'new' says that new directory for MP3 is created.

## Example

    flac2mp3.py --vbr --new 'Pink Floyd - Animals'

It converts all FLACs in 'Pink Floyd - Animals' to VBR V0 MP3s.
Flac '--new' specifies that MP3s and all non-FLAC files (artwork, logs)
should go to the new directory 'Pink Floyd - Animals (1977) [V0]'.
Original directory leaved untouched.

For inplace conversion just omit '--new' tag: MP3s will be created alongside
with
