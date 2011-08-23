#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright (C) 2010 Radim Rehurek <radimrehurek@seznam.cz>
# Licensed under the GNU LGPL v2.1 - http://www.gnu.org/licenses/lgpl.html

"""
Automated tests for checking similarity server. Assumes run_simserver.py is already
running; if not, run

$ python -m Pyro4.naming -n 0.0.0.0 &              # run Pyro naming server
$ python -m gensim.test.run_simserver /tmp/server  # create SessionServer and register it with Pyro

first.
"""

from __future__ import with_statement

import logging
import os
import os.path
import unittest
import tempfile
from copy import deepcopy

import numpy

import gensim


def mock_documents(language, category):
    """Create a few SimServer documents, for testing."""
    documents = ["Human machine interface for lab abc computer applications",
                 "A survey of user opinion of computer system response time",
                 "The EPS user interface management system",
                 "System and human system engineering testing of EPS",
                 "Relation of user perceived response time to error measurement",
                 "The generation of random binary unordered trees",
                 "The intersection graph of paths in trees",
                 "Graph minors IV Widths of trees and well quasi ordering",
                 "Graph minors A survey"]

    # Create SimServer dicts from the texts. These are the object that the gensim
    # server expects as input. They must contain doc['id'] and doc['text'] attributes.
    # All other attributes are currently ignored.
    docs = [{'id': '_'.join((language, category, str(num))),
             'text': document, 'payload': range(num), 'language': language, 'category': category}
            for num, document in enumerate(documents)]
    return docs


class SessionServerTester(unittest.TestCase):
    """Test a running SessionServer"""
    def setUp(self):
        import Pyro4 # don't import pyro at global scope; it messes up logging
        self.docs = mock_documents('en', '')
        # locate the running server on the network, using Pyro nameserver
        try:
            with Pyro4.locateNS() as ns:
                self.server = Pyro4.Proxy(ns.lookup('gensim.testserver'))
        except Pyro4.errors.PyroError, e:
            logging.error("could not locate running SessionServer: %s" % e)
            raise

    def tearDown(self):
        self.docs = None
        self.server._pyroRelease()

    def test_model(self):
        """test remote server model creation"""
        logging.debug(self.server.status())
        # calling train without specifying a training corpus raises a ValueError:
        self.assertRaises(ValueError, self.server.train, method='lsi')

        # now do the training for real. use a common pattern -- upload documents
        # to be processed to the server.
        # the documents will be stored server-side in an Sqlite db, not in memory,
        # so the training corpus may be larger than RAM.
        # if the corpus is very large, upload it in smaller chunks, like 10k docs
        # at a time (or else Pyro & cPickle will choke). also see `utils.upload_chunked`.
        self.server.buffer(self.docs[:2]) # upload 2 documents to server
        self.server.buffer(self.docs[2:]) # upload the rest of the documents

        # now, train a model
        self.server.train(method='lsi')

        # check that the model was trained correctly
        model = self.server.debug_model()
        s_values = [1.56162356, 1.39524723, 1.19488823, 1.11727727, 0.89581808,
                    0.74147441, 0.58769924, 0.39076217, 0.29696942]
        self.assertTrue(numpy.allclose(model.lsi.projection.s, s_values))

        vec0 = [(0, 0.26138668665606807), (1, -0.42474077827458095), (2, -0.37640944196377213),
                (3, 0.24878004604588472), (4, 0.7086623323932405), (5, 0.19319654259273622),
                (6, 0.080054458473849122), (7, 0.018944932880293794), (8, -0.037441525599206708)]
        got = model.doc2vec(self.docs[0])
        self.assertTrue(numpy.allclose(abs(gensim.matutils.sparse2full(vec0, model.num_features)),
                                       abs(gensim.matutils.sparse2full(got, model.num_features))))


    def check_equal(self, sims1, sims2):
        """Check that two returned lists of similarities are equal."""
        sims1, sims2 = dict(sims1), dict(sims2)
        for docid in set(sims1.keys() + sims2.keys()):
            self.assertTrue(numpy.allclose(sims1.get(docid, 0.0), sims2.get(docid, 0.0), atol=1e-7))


    def test_index(self):
        """test remote server incremental indexing"""
        # delete any existing model and indexes first
        self.server.drop_index(keep_model=False)
        logging.debug(self.server.status())

        # try indexing without a model -- raises AttributeError
        self.assertRaises(AttributeError, self.server.index, self.docs)

        # train a fresh model
        self.server.train(self.docs, method='lsi')

        # use incremental indexing -- start by indexing the first three documents
        self.server.buffer(self.docs[:3]) # upload the documents
        self.server.index() # index uploaded documents & clear upload buffer
        self.assertRaises(ValueError, self.server.find_similar, 'fakeid') # no such id -> raises ValueError

        expected =  [('en__1', 0.99999994), ('en__0', 0.25648531), ('en__2', 0.24981415)]
        got = self.server.find_similar(self.docs[1]['id']) # retrieve similar to the last document
        self.check_equal(expected, got)

        self.server.index(self.docs[3:]) # upload & index the rest of the documents
        logging.debug(self.server.status())
        expected =  [('en__1', 0.99999994), ('en__4', 0.70710671), ('en__8', 0.27910081),
                     ('en__0', 0.25648531), ('en__2', 0.24981415), ('en__3', 0.20920435),
                     ('en__7', 2.9802322e-08), ('en__6', 2.9802322e-08), ('en__5', 1.4901161e-08)]
        got = self.server.find_similar(self.docs[1]['id']) # retrieve similar to the last document
        self.check_equal(expected, got)

        # re-index documents. just index documents with the same id -- the old document
        # will be replaced by the new one, so that only the latest update counts.
        docs = deepcopy(self.docs)
        docs[2]['text'] = docs[1]['text'] # different text, same id
        self.server.index(docs[1:3]) # reindex the two modified docs -- total number of indexed docs doesn't change
        logging.debug(self.server.status())
        expected = [('en__2', 0.99999994), ('en__1', 0.99999994), ('en__4', 0.70710671),
                    ('en__8', 0.27910081), ('en__0', 0.25648531), ('en__3', 0.20920435),
                    ('en__7', 2.9802322e-08), ('en__6', 2.9802322e-08), ('en__5', 1.4901161e-08)]
        got = self.server.find_similar(self.docs[2]['id'])
        self.check_equal(expected, got)

        # delete documents: pass it a collection of ids to be removed from the index
        to_delete = [doc['id'] for doc in self.docs[-3:]]
        self.server.delete(to_delete) # delete the last 3 documents
        logging.debug(self.server.status())
        expected = [('en__2', 0.99999994), ('en__1', 0.99999994), ('en__4', 0.70710671),
                    ('en__0', 0.25648531), ('en__3', 0.20920435), ('en__5', 1.4901161e-08)]
        got = self.server.find_similar(self.docs[2]['id'])
        self.check_equal(expected, got)


    def test_optimize(self):
        # to speed up queries by id, call server.optimize()
        # it will precompute the most similar documents, for all documents in the index,
        # and store them to Sqlite db for lightning-fast querying.
        # querying by fulltext is not affected by this optimization, though.
        self.server.drop_index(keep_model=False)
        self.server.train(self.docs)
        self.server.index(self.docs)
        self.server.optimize()
        logging.debug(self.server.status())
        # TODO how to test that it's faster? just read and verify server.status()?


    def test_query_id(self):
        # index some docs first
        self.server.drop_index(keep_model=False)
        self.server.train(self.docs, method='lsi')
        self.server.index(self.docs)

        # query index by id: return the most similar documents to an already indexed document
        docid = self.docs[0]['id']
        expected = [('en__0', 1.0), ('en__2', 0.30426699), ('en__1', 0.25648531),
                    ('en__3', 0.25480536), ('en__4', 5.9604645e-08), ('en__7', 2.2351742e-08)]
        got = self.server.find_similar(docid)
        self.check_equal(expected, got)

        # same thing, but only get docs with similarity >= 0.3
        expected = [('en__0', 1.0), ('en__2', 0.30426699)]
        got = self.server.find_similar(docid, min_score=0.3)
        self.check_equal(expected, got)

        # same thing, but only get max 3 documents docs with similarity >= 0.2
        expected = [('en__0', 1.0), ('en__2', 0.30426699), ('en__1', 0.25648531)]
        got = self.server.find_similar(docid, max_results=3, min_score=0.2)
        self.check_equal(expected, got)


    def test_query_document(self):
        # index some docs first
        self.server.drop_index(keep_model=False)
        self.server.train(self.docs, method='lsi')
        self.server.index(self.docs)

        # query index by document text: id is ignored
        doc = self.docs[0]
        doc['id'] = None # clear out id; not necessary, just to demonstrate it's not used in query-by-document
        expected = [('en__0', 1.0), ('en__2', 0.30426699), ('en__1', 0.25648531),
                    ('en__3', 0.25480536), ('en__4', 5.9604645e-08), ('en__7', 2.2351742e-08)]
        got = self.server.find_similar(doc)
        self.check_equal(expected, got)

        # same thing, but only get docs with similarity >= 0.3
        expected = [('en__0', 1.0), ('en__2', 0.30426699)]
        got = self.server.find_similar(doc, min_score=0.3)
        self.check_equal(expected, got)

        # same thing, but only get max 3 documents docs with similarity >= 0.2
        expected = [('en__0', 1.0), ('en__2', 0.30426699), ('en__1', 0.25648531)]
        got = self.server.find_similar(doc, max_results=3, min_score=0.2)
        self.check_equal(expected, got)
#end SessionServerTester


if __name__ == '__main__':
    logging.basicConfig(format='%(asctime)s : %(levelname)s : %(module)s:%(lineno)d : %(funcName)s(%(threadName)s) : %(message)s')
    logging.root.level=logging.INFO
    unittest.main()
