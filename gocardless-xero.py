#!/usr/bin/env python
import re
from decimal import Decimal
from datetime import datetime, timedelta
from ConfigParser import ConfigParser

from xero import Xero
import gocardless

date_cutoff = datetime.now() - timedelta(days=300)
gcid_regex = re.compile(r'\(GCID: ([0-9A-Z]+)\)')

class GoCardlessPaymentImporter(object):

    def __init__(self, config):
        self.config = config
        self.xero = Xero(config.get('xero', 'consumer_key'),
                        config.get('xero', 'consumer_secret'),
                        config.get('xero', 'private_key_file'))

        gocardless.environment = config.get('gocardless', 'environment')
        gocardless.set_details(app_id=config.get('gocardless', 'app_id'),
                                app_secret=config.get('gocardless', 'app_secret'),
                                access_token=config.get('gocardless', 'access_token'),
                                merchant_id=config.get('gocardless', 'merchant_id'))
        self.gc_client = gocardless.client

    def do_import(self):
        print "Importing GoCardless transactions"
        posted_journals = set(self.get_posted_journals())
        to_submit = list(self.get_journals_to_submit(posted_journals))
        self.submit_journals(to_submit)
        print "Submitted %s new journal entries" % len(to_submit)

    def get_posted_journals(self):
        existing_journals = self.xero.manualjournals.filter(Since=date_cutoff, Status='POSTED')
        if existing_journals is not None:
            if not isinstance(existing_journals, (list, tuple)):
                existing_journals = [existing_journals]

            for journal in existing_journals:
                result = gcid_regex.search(journal['Narration'])
                if result:
                    yield result.groups(1)[0]

    def parse_gocardless_bill(self, bill):
        """ Convert a GoCardless bill object into a JSON representation of the Xero journal XML. """
        return {
            'Narration': "GoCardless payment from %s (GCID: %s)" % (bill.user().email, bill.id),
            'Status': 'POSTED',
            'Date': bill.paid_at.isoformat(),
            'JournalLines': [
                { 'LineAmount': str(-(Decimal(bill.amount))),
                                'AccountCode': self.config.get('xero', 'sales_account') },
                { 'LineAmount': str(Decimal(bill.gocardless_fees)), 
                                'AccountCode': self.config.get('xero', 'commission_account') },
                { 'LineAmount': str(Decimal(bill.amount) - Decimal(bill.gocardless_fees)),
                                'AccountCode': self.config.get('xero', 'gocardless_account') }
                ]
            }

    def get_journals_to_submit(self, posted_journals):
        for bill in self.gc_client.merchant().bills():
            if bill.status == 'withdrawn' and bill.id not in posted_journals:
                yield self.parse_gocardless_bill(bill)

    def submit_journals(self, to_submit):
        for start in range(0, len(to_submit), 100):
            self.xero.manualjournals.put(to_submit[start:start+100])

if __name__ == '__main__':
    config = ConfigParser()
    config.read(['xero-tools.cfg'])
    GoCardlessPaymentImporter(config).do_import()
