# -*- coding: utf-8 -*-
import numpy as np
import pandas as pd
import pickle
import time
import os
import logging
from abc import abstractmethod

from implicit.als import AlternatingLeastSquares
from joblib import Parallel, delayed
from scipy.sparse import coo_matrix, linalg
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import normalize
from sklearn.cluster import KMeans
from torch.utils.data import Dataset
from sklearn.feature_extraction import DictVectorizer
from sklearn.pipeline import Pipeline, FeatureUnion
from sklearn.base import BaseEstimator, TransformerMixin
from sklearn.metrics.pairwise import cosine_similarity

LOG_FORMAT = '%(asctime)s %(levelname)s << %(message)s'
logging.basicConfig(level=logging.DEBUG, format=LOG_FORMAT, datefmt='%H:%M:%S')


class DataProcessor(object):

    def __init__(self):
        return

    @abstractmethod
    def parse(self, df):
        raise NotImplementedError("Please implement method \'parse()\'.")

    @staticmethod
    def process(df, command, ref_df=None):
        start = time.time()

        res = None
        message = None
        if command in ['train', 'test']:
            res = TrainTestProcessor().parse(df)
            message = command
        elif command == 'members':
            res = MembersProcessor().parse(df)
            message = command
        elif command == 'songs':
            res = SongsProcessor().parse(df)
            message = command
        elif command == 'song_extra_info':
            res = SongExtraProcessor().parse(df)
            message = command
        elif command == 'engineering':
            assert ref_df is not None, 'Please pass the reference dataframe'
            res = EngineeringProcessor(ref_df).parse(df)
            message = ref_df

        assert res is not None, logging.error("command \"%s\" is valid." % command)
        logging.info("parse %s_df in %0.2fs" % (message, time.time() - start))

        return res


class SongsProcessor(DataProcessor):

    def __init__(self):
        super(SongsProcessor, self).__init__()

    def parse(self, df):
        # fill missing data
        df['artist_name'].fillna('no_artist', inplace=True)
        df['language'].fillna('nan', inplace=True)
        df['composer'].fillna('nan', inplace=True)
        df['lyricist'].fillna('nan', inplace=True)
        df['genre_ids'].fillna('nan', inplace=True)

        # feature engineering
        df['is_featured'] = df['artist_name'].apply(SongsProcessor.__is_featured).astype(np.int8)

        # >> duplicate
        df['artist_count'] = df['artist_name'].apply(SongsProcessor.__artist_count).astype(np.int8)

        df['artist_composer'] = (df['artist_name'] == df['composer'])
        df['artist_composer'] = df['artist_composer'].astype(np.int8)

        # if artist, lyricist and composer are all three same
        df['artist_composer_lyricist'] = ((df['artist_name'] == df['composer']) &
                                          (df['artist_name'] == df['lyricist']) &
                                          (df['composer'] == df['lyricist']))
        df['artist_composer_lyricist'] = df['artist_composer_lyricist'].astype(np.int8)

        # >> duplicate
        df['song_lang_boolean'] = df['language'].apply(SongsProcessor.__song_lang_boolean).astype(np.int8)

        # howeverforever
        df['genre_count'] = df['genre_ids'].apply(SongsProcessor.__parse_splitted_category_to_number)
        df['composer_count'] = df['composer'].apply(SongsProcessor.__parse_splitted_category_to_number)
        df['lyricist_count'] = df['lyricist'].apply(SongsProcessor.__parse_splitted_category_to_number)

        df['1h_lang'] = df['language'].apply(SongsProcessor.__one_hot_encode_lang)

        df['1h_song_length'] = df['song_length'].apply(lambda x: 1 if x <= 239738 else 0)

        assert(~df.isnull().any().any()), 'There exists missing data!'

        return df

    @staticmethod
    def __is_featured(x):
        return 1 if 'feat' in str(x) else 0

    @staticmethod
    def __artist_count(x):
        return 0 if x == 'no_artist' else x.count('and') + x.count(',') + x.count('feat') + x.count('&')

    @staticmethod
    def __song_lang_boolean(x):
        # is song language 17 or 45.
        return 1 if '17.0' in str(x) or '45.0' in str(x) else 0

    @staticmethod
    def __parse_splitted_category_to_number(x):
        if x is np.nan:
            return 0
        x = str(x)
        x.replace('/', '|')
        x.replace(';', '|')
        x.replace('\\', '|')
        x.replace(' and ', '|')
        x.replace('&', '|')
        x.replace('+', '|')
        return x.count('|') + 1

    @staticmethod
    def __one_hot_encode_lang(x):
        return 1 if x in [-1, 17, 45] else 0


class MembersProcessor(DataProcessor):

    def __init__(self):
        super(MembersProcessor, self).__init__()

    def parse(self, df):
        # fill missing data
        df['gender'].fillna('nan', inplace=True)

        # feature engineering
        df['membership_days'] = df['expiration_date'].subtract(df['registration_init_time']).dt.days.astype(int)

        df['registration_year'] = df['registration_init_time'].dt.year
        df['registration_month'] = df['registration_init_time'].dt.month

        df['expiration_year'] = df['expiration_date'].dt.year
        df['expiration_month'] = df['expiration_date'].dt.month

        # useless feature
        df.drop(['registration_init_time'], axis=1, inplace=True)

        # howeverforever
        df['bd'] = df['bd'].apply(MembersProcessor.__transform_bd_outliers)
        df['1h_via'] = df['registered_via'].apply(MembersProcessor.__one_hot_encode_via)

        assert (~df.isnull().any().any()), 'There exists missing data!'

        return df

    @staticmethod
    def __transform_bd_outliers(bd):
        # figure is from "exploration"
        if bd >= 120 or bd <= 7:
            return 'nan'
        mean = 28.99737187910644
        std = 9.538470787507382
        return bd if abs(bd - mean) <= 3 * std else 'nan'

    @staticmethod
    def __one_hot_encode_via(x):
        return 0 if x == 4 else 1


class SongExtraProcessor(DataProcessor):

    def __init__(self):
        super(SongExtraProcessor, self).__init__()

    def parse(self, df):
        df['song_year'] = df['isrc'].apply(SongExtraProcessor.__transform_isrc_to_year)
        df.drop(['name', 'isrc'], axis=1, inplace=True)

        # howeverforever
        # df['song_country'] = df['isrc'].apply(self._transform_isrc_to_country)
        # df['song_registration'] = df['isrc'].apply(self._transform_isrc_to_reg)
        # df['song_designation'] = df['isrc'].apply(self._transform_isrc_to_desig)

        df['1h_song_year'] = df['song_year'].apply(SongExtraProcessor.__one_hot_encode_year)
        # df['1h_song_country'] = df['song_country'].apply(self._one_hot_encode_country)

        df['song_year'].fillna(2017, inplace=True)
        # df['song_registration'].fillna('***', inplace=True)

        assert (~df.isnull().any().any())

        return df

    @staticmethod
    def __transform_isrc_to_year(isrc):
        if type(isrc) != str:
            return np.nan
        # this year 2017
        suffix = int(isrc[5:7])
        return 1900 + suffix if suffix > 17 else 2000 + suffix

    @staticmethod
    def __one_hot_encode_year(x):
        return 1 if 2013 <= float(x) <= 2017 else 0


class TrainTestProcessor(DataProcessor):

    def __init__(self):
        super(TrainTestProcessor, self).__init__()

    def parse(self, df):
        # fill missing data
        df['source_system_tab'].fillna('others', inplace=True)
        df['source_screen_name'].fillna('others', inplace=True)
        df['source_type'].fillna('nan', inplace=True)

        # feature engineering
        df['source_merged'] = df['source_system_tab'].map(str) + ' | ' +\
                              df['source_screen_name'].map(str) + ' | ' +\
                              df['source_type'].map(str)

        count_df = df[['source_merged', 'target']].groupby('source_merged').agg(['mean', 'count'])
        count_df.reset_index(inplace=True)
        count_df.columns = ['source_merged', 'source_replay_pb', 'source_replay_count']

        df = df.merge(count_df, on='source_merged', how='left')

        df['1h_source'] = df['source_replay_pb'].apply(TrainTestProcessor.__one_hot_encode_source)

        df['1h_system_tab'] = df['source_system_tab'].apply(TrainTestProcessor.__one_hot_encode_system_tab)
        df['1h_screen_name'] = df['source_screen_name'].apply(TrainTestProcessor.__one_hot_encode_screen_name)
        df['1h_source_type'] = df['source_type'].apply(TrainTestProcessor.__one_hot_encode_source_type)

        # useless feature
        df.drop(['source_merged', 'source_replay_pb', 'source_replay_count'], axis=1, inplace=True)

        assert (~df.isnull().any().any()), 'There exists missing data!'

        return df

    @staticmethod
    def __one_hot_encode_system_tab(x):
        return 1 if x == 'my library' else 0

    @staticmethod
    def __one_hot_encode_screen_name(x):
        return 1 if x == 'Local playlist more' or x == 'My library' else 0

    @staticmethod
    def __one_hot_encode_source_type(x):
        return 1 if x == 'local-library' or x == 'local-playlist' else 0

    @staticmethod
    def __one_hot_encode_source(x):
        return 1 if x >= 0.6 else 0


class EngineeringProcessor(DataProcessor):

    def __init__(self, ref_df):
        super(EngineeringProcessor, self).__init__()
        self.ref_df = ref_df

    def parse(self, df):
        df = self.generate_play_count(df)
        df = self.generate_track_count(df)
        df = self.generate_cover_lang(df)

        return df

    def generate_play_count(self, df):
        count_df = self.ref_df['song_id'].value_counts().reset_index()
        count_df.columns = ['song_id', 'play_count']

        df = df.merge(count_df, on='song_id', how='left')
        df['play_count'].fillna(0, inplace=True)

        return df

    def generate_track_count(self, df):
        track_count_df = self.ref_df[['song_id', 'artist_name']].drop_duplicates('song_id')
        track_count_df = track_count_df.groupby('artist_name').agg('count').reset_index()
        track_count_df.columns = ['artist_name', 'track_count']
        track_count_df = track_count_df.sort_values('track_count', ascending=False)

        artist_count_df = df[['artist_name', 'target']].groupby('artist_name').agg(['mean', 'count']).reset_index()
        artist_count_df.columns = ['artist_name', 'replay_pb', 'play_count']

        artist_count_df = artist_count_df.merge(track_count_df, on='artist_name', how='left')

        df = df.merge(artist_count_df[['artist_name', 'track_count']], on='artist_name', how='left')
        df['track_count'].fillna(0, inplace=True)

        return df

    def generate_cover_lang(self, df):
        cover_lang_df = self.ref_df[['artist_name', 'language']].drop_duplicates(['artist_name', 'language'])
        cover_lang_df = cover_lang_df['artist_name'].value_counts().reset_index()
        cover_lang_df.columns = ['artist_name', 'cover_lang']

        df = df.merge(cover_lang_df, on='artist_name', how='left')
        df['cover_lang'].fillna(0, inplace=True)

        return df


class FeatureProcessor(object):

    __SONGS_FILE_NAME = 'songs.csv'
    __SONG_EXTRA_FILE_NAME = 'song_extra_info.csv'
    __MEMBERS_FILE_NAME = 'members.csv'
    __TRAIN_FILE_NAME = 'train.csv'
    __TEST_FILE_NAME = 'test.csv'

    __INITIALIZATION_READY = 0
    __LOAD_READY = 1
    __PREPROCESS_READY = 2
    __ENGINEERING_READY = 3

    def __init__(self, root='./data'):
        assert os.path.exists(root), '%s not exists!' % root
        self._root = os.path.expanduser(root)

        self._songs_df = None
        self._song_extra_info_df = None
        self._members_df = None
        self._train_df = None
        self._test_df = None
        self._comb_df = None
        self._state = FeatureProcessor.__INITIALIZATION_READY

    def load_raw(self):
        """
        Load all raw data under the directory specified.
        Call this function right after initialization.

        :return:
        """

        assert self._state >= FeatureProcessor.__INITIALIZATION_READY, logging.error("Please reconstruct new class")

        start = time.time()

        # load train & test set
        self._train_df = pd.read_csv(os.path.join(self._root, self.__TRAIN_FILE_NAME))
        self._test_df = pd.read_csv(os.path.join(self._root, self.__TEST_FILE_NAME))

        # load song & member set
        self._songs_df = pd.read_csv(os.path.join(self._root, self.__SONGS_FILE_NAME))
        self._song_extra_info_df = pd.read_csv(os.path.join(self._root, self.__SONG_EXTRA_FILE_NAME))
        self._members_df = pd.read_csv(os.path.join(self._root, self.__MEMBERS_FILE_NAME),
                                       parse_dates=['registration_init_time', 'expiration_date'])

        self._state = FeatureProcessor.__LOAD_READY
        logging.info("load raw data in %0.2fs" % (time.time() - start))

    def pre_process(self):
        """
        Pre-process all dataframes and merge them into "train_df" and "test_df".
        Call this function after calling "load_raw"

        :return:
        """

        assert self._state >= FeatureProcessor.__LOAD_READY, logging.error("Please load raw data first")

        # pre-process all data-frame
        self._train_df = DataProcessor().process(self._train_df, 'train')
        self._test_df = DataProcessor().process(self._train_df, 'test')
        self._members_df = DataProcessor().process(self._members_df, 'members')
        self._songs_df = DataProcessor().process(self._songs_df, "songs")
        self._song_extra_info_df = DataProcessor().process(self._song_extra_info_df, "song_extra_info")

        # merge all data-frame
        self._songs_df = self._songs_df.merge(self._song_extra_info_df, on='song_id', how='left')

        self._train_df = self._train_df.merge(self._songs_df, on='song_id', how='left')
        self._test_df = self._test_df.merge(self._songs_df, on='song_id', how='left')

        self._train_df = self._train_df.merge(self._members_df, on='msno', how='left')
        self._test_df = self._test_df.merge(self._members_df, on='msno', how='left')

        self._comb_df = self._train_df.append(self._test_df)

        self._state = FeatureProcessor.__PREPROCESS_READY

    def feature_engineering(self):
        """
        Do the advanced feature engineering.
        Call this function after calling "pre_process"

        :return:
        """

        assert self._state >= FeatureProcessor.__PREPROCESS_READY, logging.error("Please proprocess raw data first")

        self._train_df = DataProcessor().process(self._train_df, 'engineering', self._train_df)
        self._test_df = DataProcessor().process(self._test_df, 'engineering', self._comb_df)

        self._state = FeatureProcessor.__ENGINEERING_READY


def main():
    fp = FeatureProcessor(root='../data')
    fp.load_raw()
    fp.pre_process()
    fp.feature_engineering()


if __name__ == '__main__':
    main()
