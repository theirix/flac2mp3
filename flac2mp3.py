#!/usr/bin/env python3
"""
Recode and retag FLACs to MP3 for releasing at torrent tracker.

Usage:
    flac2mp3.py [-h] [--vbr|--cbr] [--new] path

Example:
    flac2mp3.py --vbr --new 'Pink Floyd - Animals'

It converts all FLACs in 'Pink Floyd - Animals' to VBR V0 MP3s.
Flac '--new' specifies that MP3s and all non-FLAC files (artwork, logs)
should go to the new directory 'Pink Floyd - Animals (1977) [V0]'.
Original directory leaved untouched.

For inplace conversion just omit '--new' tag: MP3s will be created alongside
with FLACs.
"""

import os
import subprocess
import mimetypes
from pathlib import Path
from shutil import copytree, ignore_patterns
from argparse import ArgumentParser
from mutagen.flac import FLAC
from mutagen.easyid3 import EasyID3
# pylint: disable=no-name-in-module
# noinspection PyUnresolvedReferences
from mutagen.id3 import ID3, Encoding, PictureType, APIC
# pylint: disable=no-member
from colorama import Fore, Style, init as colorama_init


class Taginfo:
    """ Taginfo class
    Facade for FLAC tag info
    """

    def __init__(self, flac_path):
        self.flac = FLAC(flac_path)
        # cache for flac keys, normalized and deduplicated
        self.flac_keys = [key.upper() for [key, _] in
                          self.flac.tags if len(self.flac[key][0].strip()) > 0]
        if ('ALBUM ARTIST' in self.flac_keys and
                'ALBUMARTIST' in self.flac_keys and
                self.flac['ALBUM ARTIST'] == self.flac['ALBUMARTIST']):
            self.flac_keys.remove('ALBUM ARTIST')

    def consume(self, key):
        """Read and then delete a tag from cached tags
        Also got only first tag for multiple FLAC tags
        """

        value = self.flac[key]
        self.flac_keys.remove(key)
        if isinstance(value, list):
            return value[0]
        return value

    def has(self, key):
        """ Accessor for flac key """
        if key in self.flac_keys:
            return key is not None and key != ''
        return False

    def get_discnumber(self):
        """ Extract discnumber or 1 if missing """
        if self.has('DISCNUMBER'):
            return int(self.consume('DISCNUMBER'))
        return 1

    def get_release_dir_name(self, mp3_mode):
        """ Compose a directory name for specific mp3_mode (V0 or 320) """
        for required in ('ARTIST', 'ALBUM', 'DATE'):
            if not self.has(required):
                raise Exception('Not {} information to craft dir name'.format(required))
        return "{} - {} ({}) [{}]".format(
            self.consume('ARTIST'), self.consume('ALBUM'), self.consume('DATE'), mp3_mode)


class Retagger(Taginfo):
    """ Retagger class
    Performs extaction tags from FLAC and setting to MP3
    Cares about track and disc count, artist name, duplicate tags
    """

    def __init__(self, flac_path, mp3_path, count, multidisc, verbose):
        # Initialize and cache tags
        Taginfo.__init__(self, flac_path)
        self.id3 = EasyID3(mp3_path)
        self.count = count
        self.multidisc = multidisc
        self.verbose = verbose

    def __set_tracks(self):
        # set track number
        if 'TRACKNUMBER' in self.flac_keys:
            # TRACKTOTAL is the prefered field
            if 'TRACKTOTAL' in self.flac_keys:
                self.id3['tracknumber'] = '/'.join([self.consume('TRACKNUMBER'),
                                                    self.consume('TRACKTOTAL')])
            elif 'TOTALTRACKS' in self.flac_keys:
                self.id3['tracknumber'] = '/'.join([self.consume('TRACKNUMBER'),
                                                    self.consume('TOTALTRACKS')])
            elif self.count:
                self.id3['tracknumber'] = '/'.join([self.consume('TRACKNUMBER'),
                                                    str(self.count)])

    def __set_discs(self):
        # set disc number only if multiple discs
        if (self.multidisc and ('DISCNUMBER' in self.flac_keys) and
                ('DISCTOTAL' in self.flac_keys or 'TOTALDISCS' in self.flac_keys)):
            print('Setting disc number')
            if 'DISCTOTAL' in self.flac_keys:
                self.id3['discnumber'] = '/'.join([self.consume('DISCNUMBER'),
                                                   self.consume('DISCTOTAL')])
            elif 'TOTALDISCS' in self.flac_keys:
                self.id3['discnumber'] = '/'.join([self.consume('DISCNUMBER'),
                                                   self.consume('TOTALDISCS')])

        # drop discs stuff at all if not multidisc
        if not self.multidisc:
            for drop in ['DISCNUMBER', 'TOTALDISCS', 'DISCTOTAL']:
                if drop in self.flac_keys:
                    self.flac_keys.remove(drop)

    def retag(self):
        """ Perform tagging """
        # set essentials
        for key in ['ARTIST', 'TITLE', 'ALBUM', 'ALBUMARTIST',
                    'PERFORMER', 'COMPOSER', 'DATE', 'GENRE']:
            if key in self.flac_keys:
                self.id3[key] = self.consume(key)

        # Set and clean track and disc count
        self.__set_tracks()

        self.__set_discs()

        # drop definitely unneeded tags
        for key in ['DISCNUMBER', 'TOTALDISCS', 'DISCTOTAL',
                    'TRACKNUMBER', 'TOTALTRACKS', 'TRACKTOTAL']:
            if key in self.flac_keys:
                self.consume(key)

        # check for known remaining tags
        for key in self.flac_keys[:]:
            if key.lower() in EasyID3.valid_keys.keys():
                print('Adding known tag: %s=%s' % (key, self.flac[key]))
                self.id3[key] = self.consume(key)

        # add remaining keys to TXXX frames
        for key in self.flac_keys[:]:
            print('Adding custom tag: %s=%s' % (key, self.flac[key]))
            EasyID3.RegisterTXXXKey(key, key)
            self.id3[key] = self.consume(key)

        self.id3.save(v2_version=3)
        if self.verbose:
            print('Tags written')


class Recoder:
    """ Recoder class
    Performs FLAC to MP3 recoding and proper file naming
    """

    def __init__(self, flags):
        self.flags = flags

    def recode_dir(self, path):
        """ Mode: recode a dir inplace """
        # Enumerate flacs
        flacs = sorted([x for x in Path(path).iterdir() if x.suffix == '.flac'])
        is_multidisc = self.__get_multidisc(list(flacs))
        if len([x for x in flacs if str(x).count('"') > 0]):
            raise Exception('Quotes in names, cannot continue')
        # Finally recode
        for idx, flac in enumerate(flacs):
            mp3 = flac.with_suffix('.mp3')
            self.__recode_file_impl(flac, mp3, idx, len(flacs), is_multidisc)

    def recode_new_dir(self, path, target):
        """ Mode: recode a dir to a new dir with a proper name """

        # Enumerate flacs
        flacs = sorted([x for x in Path(path).iterdir() if x.suffix == '.flac'])
        is_multidisc = self.__get_multidisc(list(flacs))
        if len([x for x in flacs if str(x).count('"') > 0]):
            raise Exception('Quotes in names, cannot continue')
        # Compose new name
        if self.flags.vbr:
            mode_str = 'V0'
        elif self.flags.cbr:
            mode_str = '320'
        else:
            raise Exception('Wrong mode str')
        if target:
            target_parent_path = Path(target).expanduser().resolve()
        else:
            target_parent_path = Path(path).resolve().parent
        new_path = target_parent_path / Path(Taginfo(str(flacs[0])).
                                             get_release_dir_name(mode_str))
        if new_path.is_dir():
            raise Exception('Target already exists: %s' % new_path)
        print("New path is %s" % str(new_path))
        # Copy artwork, cues etc
        copytree(str(path), str(new_path), ignore=ignore_patterns('*.flac'))
        print("Copied %s files and dirs" % len([x for x in Path(new_path).iterdir()]))
        # Finally recode
        for idx, flac in enumerate(flacs):
            mp3 = new_path / flac.with_suffix('.mp3').name
            self.__recode_file_impl(flac, mp3, idx, len(flacs), is_multidisc)

    def recode_file(self, flac):
        """ Mode: recode a file """
        mp3 = Path(flac).with_suffix('.mp3')
        self.__recode_file_impl(Path(flac), mp3, None, False, None)

    def __recode_file_impl(self, flac, mp3, idx, count, multidisc):
        """ Recode file, set tags and image """
        width = (count // 10) + 1
        print("{}--- [{:0{}d}/{:0{}d}] {}{}".format(Fore.GREEN, idx+1, width, count, width, str(flac), Style.RESET_ALL))
        self.__recode_to_mp3(flac, mp3)
        retagger = Retagger(str(flac), str(mp3), count, multidisc, self.flags.verbose)
        retagger.retag()
        image_path = flac.parent / 'folder.jpg'
        if image_path.exists():
            self.__set_image(mp3, image_path)
        else:
            image_path = flac.parent / 'cover.jpg'
            if image_path.exists():
                self.__set_image(mp3, image_path)
        self.__post_check(mp3)

    @staticmethod
    def __get_multidisc(flac_pathes):
        return max([Taginfo(str(flac_path)).get_discnumber() for flac_path in flac_pathes]) > 1

    def __set_image(self, mp3, image_path):
        """ Set image to MP3 tag from file """
        with open(str(image_path), "rb") as image_io:
            data = image_io.read()
        mime = mimetypes.guess_type(str(image_path))[0] or "image/jpeg"
        frame = APIC(encoding=Encoding.UTF8, mime=mime,
                     desc=u"cover", type=PictureType.COVER_FRONT,
                     data=data)

        id3tag = ID3(str(mp3), v2_version=3)
        id3tag.add(frame)
        id3tag.save(v2_version=3)
        if self.flags.verbose:
            print('Images written')

    @staticmethod
    def __post_check(mp3):
        """ Tag sanity check """
        id3tag = EasyID3(str(mp3))
        for tag in ['artist', 'album', 'title', 'genre', 'date']:
            if tag not in id3tag:
                print(Fore.RED + 'No essential tag found: ' + tag + Style.RESET_ALL)

    def __recode_to_mp3(self, flac, mp3):
        """ Recode from FLAC to MP3 """
        if flac.suffix != '.flac':
            raise Exception('Wrong path')
        if mp3.exists():
            if self.flags.force:
                mp3.unlink()
            else:
                raise Exception('Path %s already exists' % mp3)
        # encode
        tmp_wav = (mp3.parent / "tmp.wav")
        wav_path = str(tmp_wav)
        flac_path = str(flac)
        mp3_path = str(mp3)
        try:
            cmd = "flac \"%s\" -d --silent --force -o \"%s\"" % (flac_path, wav_path)
            if self.flags.verbose:
                print('Decoding: %s' % cmd)
            subprocess.check_call(cmd, shell=True)
            lame_settings = ''
            if self.flags.vbr:
                lame_settings = '-V 0 --vbr-new'
            elif self.flags.cbr:
                lame_settings = '-b 320'
            elif self.flags.mode:
                lame_settings = self.flags.mode
            cmd = "lame --silent -q 0 \"%s\" --add-id3v2 --id3v2-only \"%s\" \"%s\"" % \
                  (lame_settings, wav_path, mp3_path)
            if self.flags.verbose:
                print('Encoding: %s' % cmd)
            subprocess.check_call(cmd, shell=True)
            tmp_wav.unlink()
        except Exception:  # pylint: disable=broad-except
            if mp3.exists():
                mp3.unlink()
            if tmp_wav.exists():
                tmp_wav.unlink()
            raise


def main():
    """ Entry point """
    colorama_init()

    parser = ArgumentParser()
    parser.add_argument('--mode', '-m', help='LAME mode')
    parser.add_argument('--vbr', action='store_true', help='VBR V0 mode')
    parser.add_argument('--cbr', action='store_true', help='CBR 320 mode')
    parser.add_argument('--new', action='store_true', help='Create new dir')
    parser.add_argument('--target', type=str, help='Target directory')
    parser.add_argument('--force', '-f', action='store_true', help='Overwrite mp3')
    parser.add_argument('--verbose', '-v', action='store_true', help='Verbose')
    parser.add_argument('path', help='Directory or file path')
    flags = parser.parse_args()

    # Create a recoder for one of three types:
    #    - recode a flacs dir to a new dir
    #    - recode a flacs dir inplace
    #    - recode a file
    recoder = Recoder(flags)
    if os.path.isdir(flags.path) and flags.new:
        recoder.recode_new_dir(flags.path, flags.target)
    elif os.path.isdir(flags.path):
        recoder.recode_dir(flags.path)
    else:
        recoder.recode_file(flags.path)
    print(Fore.GREEN + 'Done' + Style.RESET_ALL)


if __name__ == '__main__':
    main()
