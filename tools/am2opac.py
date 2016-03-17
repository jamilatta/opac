#!/usr/bin/python
# coding: utf-8

import os
import sys
import textwrap
import optparse
import datetime
import logging.config
from lxml import etree
from uuid import uuid4
from datetime import timedelta

import packtools
from mongoengine import connect

from opac_schema.v1 import models
from mongoengine import Q
from thrift_clients import clients

logger = logging.getLogger(__name__)

FROM_DATE = (datetime.datetime.now()-timedelta(60)).isoformat()[:10]

XML_CSS = "/static/css/style_article_html.css"

XML_SAMPLE = etree.parse(open('xml_sample.xml'))


def _config_logging(logging_level='INFO', logging_file=None):

    allowed_levels = {
        'DEBUG': logging.DEBUG,
        'INFO': logging.INFO,
        'WARNING': logging.WARNING,
        'ERROR': logging.ERROR,
        'CRITICAL': logging.CRITICAL
    }

    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    logger.setLevel(allowed_levels.get(logging_level, 'INFO'))

    if logging_file:
        hl = logging.FileHandler(logging_file, mode='a')
    else:
        hl = logging.StreamHandler()

    hl.setFormatter(formatter)
    hl.setLevel(allowed_levels.get(logging_level, 'INFO'))

    logger.addHandler(hl)

    return logger


class AM2Opac(object):
    """
    Process to load data from Article Meta to MongoDB using OPAC Schema v1.
    """

    usage = """\
    %prog -f (full) load all data from Article Meta OR -d (distinct) only
    difference between endpoints.

    This process collects all Journal, Issues, Articles in the Article meta
    http://articlemeta.scielo.org and load in MongoDB using OPAC Schema.

    With this process it is possible to process all data or only difference
    between MongoDb and Article Meta, use pcitations -h to verify the options
    list.
    """

    parser = optparse.OptionParser(
        textwrap.dedent(usage), version="prog 0.1 - beta")

    parser.add_option('-f', '--full', action='store_true',
                      help='Update all Journals, Issues and Articles')

    parser.add_option('-r', '--rebuild_index', action='store_true',
                      help='This will remove all data in MongoDB and load all\
                      Journals, Issues and Articles')

    parser.add_option(
        '--from_date',
        '-b',
        default=FROM_DATE,
        help='From processing date (YYYY-MM-DD). Default (%s)' % FROM_DATE
    )

    parser.add_option(
        '--logging_file',
        '-o',
        help='Full path to the log file'
    )

    parser.add_option(
        '-c', '--collection',
        dest='collection',
        default=None,
        help='use the acronym of the collection eg.: spa, scl, col.')

    parser.add_option(
        '--logging_level',
        '-l',
        default='DEBUG',
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'],
        help='Logging level'
    )

    def __init__(self, argv):
        self.started = None
        self.finished = None

        self.options, self.args = self.parser.parse_args(argv)

        _config_logging(self.options.logging_level, self.options.logging_file)

        self.articlemeta = clients.ArticleMeta('articlemeta.scielo.org', 11720)

        self.m_conn = connect('manager2mongo')

    def _duration(self):
        """
        Return datetime process duration
        """
        return self.finished - self.started

    def _trydate(self, str_date):
        """
        Try to convert like string date to datetime.

        Possibilities: 2010, 2010-10, 2010-1-2
        """
        list_date = str_date.split('-')

        if len(list_date) == 1:
            return datetime.datetime.strptime(str_date, '%Y')
        elif len(list_date) == 2:
            return datetime.datetime.strptime(str_date, '%Y-%M')
        elif len(list_date) == 3:
            return datetime.datetime.strptime(str_date, '%Y-%M-%d')

    def _transform_collection(self, collection):
        """
        Transform ``articlemeta.thrift.collection`` to ``opac_scielo.Collection``.
        param: collection is the acronym of collection.
        """

        m_collection = models.Collection()
        m_collection._id = str(uuid4().hex)
        m_collection.acronym = collection.acronym
        m_collection.name = collection.name

        # Default license ``CC-BY`` need to get it from Article Meta.
        m_collection.license_code = 'CC-BY'

        return m_collection

    def _transform_journal(self, journal):
        """
        Transform ``xylose.scielodocument.Journal`` to ``opac_scielo.Journal``.
        """
        m_journal = models.Journal()

        # We have to define which id will be use to legacy journals.
        _id = str(uuid4().hex)
        m_journal._id = _id
        m_journal.jid = _id

        m_journal.created = datetime.datetime.now()
        m_journal.updated = datetime.datetime.now()

        # Set collection
        collection = models.Collection.objects.get(
            acronym__iexact=journal.collection_acronym)
        m_journal.collection = collection

        m_journal.subject_categories = journal.subject_areas
        m_journal.study_areas = journal.wos_subject_areas
        m_journal.current_status = journal.current_status
        m_journal.publisher_city = journal.publisher_loc

        # Alterar no opac_schema, pois o xylose retorna uma lista e o opac_schema
        # aguarda um string.
        m_journal.publisher_name = journal.publisher_name[0]
        m_journal.eletronic_issn = journal.electronic_issn
        m_journal.scielo_issn = journal.scielo_issn
        m_journal.print_issn = journal.print_issn
        m_journal.acronym = journal.acronym

        # Create Use License
        if journal.permissions:
            ulicense = models.UseLicense()
            ulicense.license_code = journal.permissions['id']
            ulicense.reference_url = journal.permissions['url']
            ulicense.disclaimer = journal.permissions['text']

            m_journal.use_licenses = ulicense

        m_journal.title = journal.title
        m_journal.title_iso = journal.abbreviated_iso_title

        missions = []
        for lang, des in journal.mission.items():
            m = models.Mission()
            m.language = lang
            m.description = des
            missions.append(m)

        m_journal.mission = missions

        timelines = []
        for status in journal.status_history:
            timeline = models.Timeline()
            timeline.reason = status[2]
            timeline.status = status[1]

            # Corrigir datetime
            timeline.since = self._trydate(status[0])
            timelines.append(timeline)

        m_journal.timeline = timelines
        m_journal.short_title = journal.abbreviated_title
        m_journal.index_at = journal.wos_citation_indexes
        m_journal.updated = self._trydate(journal.update_date)
        m_journal.created = self._trydate(journal.creation_date)
        m_journal.copyrighter = journal.copyrighter
        m_journal.publisher_country = journal.publisher_country[1]
        m_journal.online_submission_url = journal.submission_url
        m_journal.publisher_state = journal.publisher_state
        m_journal.sponsors = journal.sponsors

        if journal.other_titles:
            other_titles = []
            for title in journal.other_titles:
                t = models.OtherTitle()
                t.title = title
                t.category = "other"
                other_titles.append(t)

            m_journal.other_titles = other_titles

        return m_journal

    def _transform_issue(self, issue):
        """
        Transform ``xylose.scielodocument.issue`` to ``opac_scielo.Issue``.
        """

        m_issue = models.Issue()

        # We have to define which id will be use to legacy journals.
        _id = str(uuid4().hex)
        m_issue._id = _id
        m_issue.iid = _id

        m_issue.created = datetime.datetime.now()
        m_issue.updated = datetime.datetime.now()

        m_issue.unpublish_reason = issue.publisher_id

        # Get Journal of the issue
        journal = models.Journal.objects.get(scielo_issn=issue.journal.scielo_issn)
        m_issue.journal = journal

        m_issue.volume = issue.volume
        m_issue.number = issue.number

        m_issue.type = issue.type

        m_issue.start_month = issue.start_month
        m_issue.end_month = issue.end_month
        m_issue.year = int(issue.publication_date[:4])

        m_issue.label = issue.label
        m_issue.order = issue.order

        m_issue.bibliographic_legend = '%s. vol.%s no.%s %s %s./%s. %s' % (issue.journal.abbreviated_title, issue.volume, issue.number, issue.journal.publisher_state, issue.start_month, issue.end_month, issue.publication_date[:4])

        m_issue.pid = issue.publisher_id

        return m_issue

    def _transform_article(self, article):
        """
        Transform ``xylose.scielodocument.article`` to ``opac_scielo.Article``.
        """

        m_article = models.Article()

        _id = str(uuid4().hex)
        m_article._id = _id
        m_article.aid = _id

        try:
            # artigo que referenciam ou são um ahead não devem ser cadastrados
            issue = models.Issue.objects.get(
                pid=article.issue.publisher_id)
            m_article.issue = issue
        except models.DoesNotExist as e:
            logger.warning("Article without issue %s" % str(article.publisher_id))

        journal = models.Journal.objects.get(
            scielo_issn=article.journal.scielo_issn)
        m_article.journal = journal

        m_article.title = article.original_title()

        # Corrigir é necessário cadastrarmos todos as seções com todos os idiomas

        subjects = []
        sections = []

        if article.translated_section():
            for lang, label in article.translated_section().iteritems():
                subject = models.Subject()
                subject.name = label
                subject.language = lang
                subjects.append(subject)

                section = models.Section()
                section.subjects = subjects
                sections.append(section)

            m_article.sections = sections

        if article.translated_titles():
            translated_titles = []

            for lang, title in article.translated_titles().items():
                trasnlated_title = models.TranslatedTitle()
                trasnlated_title.language = lang
                trasnlated_title.name = title
                translated_titles.append(trasnlated_title)

            m_article.translated_titles = translated_titles

        m_article.section = article.original_section()

        try:
            m_article.order = int(article.order)
        except ValueError as e:
            logger.warning('Invalid order: %s-%s' % (e, article.publisher_id))

        m_article.doi = article.doi
        m_article.is_aop = article.is_ahead_of_print

        m_article.created = datetime.datetime.now()
        m_article.updated = datetime.datetime.now()

        htmls = []
        try:
            for lang, output in packtools.HTMLGenerator(XML_SAMPLE, valid_only=False, css=XML_CSS):
                source = etree.tostring(output, encoding="utf-8",
                                        method="html", doctype=u"<!DOCTYPE html>")
                html_doc = models.ArticleHTML()
                html_doc.language = lang
                html_doc.source = source
                htmls.append(html_doc)
        except Exception as e:
            print "Article pid: %s, sem html, Error: %s" % (article.publisher_id, e.message)

        m_article.htmls = htmls

        m_article.pid = article.publisher_id

        return m_article

    def _bulk(self):

        models.Collection.objects.all().delete()
        models.Journal.objects.all().delete()
        models.Issue.objects.all().delete()
        models.Article.objects.all().delete()

        logger.info("Get collection %s." % self.options.collection)

        # Collection
        for col in self.articlemeta.collections():
            if col.acronym == self.options.collection:
                self._transform_collection(col).save()

        logger.info('Get all journals...')

        # Journal
        for journal in self.articlemeta.journals(collection=self.options.collection):
            journal = self._transform_journal(journal).save()

            logger.info("Journal %s-%s" % (journal.scielo_issn, journal.title_iso))

        logger.info('Get all issues...')

        # Issue
        for issue in self.articlemeta.issues(collection=self.options.collection):
            if issue.is_press_release:
                continue
            self._transform_issue(issue).save()

        logger.info('Get all articles...')

        # Get last issue for each Journal
        for journal in models.Journal.objects.all():

            logger.info('Get last issue for journal: %s' % journal.title)

            issue = models.Issue.objects.filter(journal=journal).order_by('-year', '-order').first()
            issue_count = models.Issue.objects.filter(journal=journal).count()

            last_issue = self.articlemeta.get_issue(code=issue.pid)

            m_last_issue = models.LastIssue()
            m_last_issue.volume = last_issue.volume
            m_last_issue.number = last_issue.number
            m_last_issue.year = last_issue.publication_date[:4]
            m_last_issue.start_month = last_issue.start_month
            m_last_issue.end_month = last_issue.end_month
            m_last_issue.iid = issue.iid
            m_last_issue.bibliographic_legend = '%s. vol.%s no.%s %s %s./%s. %s' % (issue.journal.title_iso, issue.volume, issue.number, issue.journal.publisher_state, issue.start_month, issue.end_month, issue.year)

            if last_issue.sections:
                sections = []
                for code, items in last_issue.sections.iteritems():
                    if items:
                        section = models.Section()
                        subjects = []
                        for k, v in items.iteritems():
                            subject = models.Subject()
                            subject.name = v
                            subject.language = k
                            subjects.append(subject)
                    section.subjects = subjects
                    sections.append(section)

            m_last_issue.sections = sections

            journal.last_issue = m_last_issue
            journal.issue_count = issue_count
            journal.save()

        # Article
        for article in self.articlemeta.articles(collection=self.options.collection):
            self._transform_article(article).save()

    def run(self):
        """
        Run the Loaddata switching between full and incremental indexation
        """

        self.started = datetime.datetime.now()

        logger.info('Load Data from Article Meta to MongoDB')

        if self.options.full:
            logger.info('You have selected full processing... this will take a while')

            self._bulk()

        self.finished = datetime.datetime.now()

        logger.info("Total processing time: %s sec." % self._duration())


def main(argv=sys.argv[1:]):

    command = AM2Opac(argv)

    return command.run()

if __name__ == '__main__':
    main(sys.argv)