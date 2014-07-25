#!/usr/bin/env python
import re
from decimal import Decimal
from datetime import datetime, timedelta
from ConfigParser import ConfigParser

from xero import Xero
from xero.auth import PrivateCredentials
import gocardless

date_cutoff = datetime.now() - timedelta(days=300)
gcid_regex = re.compile(r'\GoCardless payout ([0-9A-Z]+)')


class GoCardlessPaymentImporter(object):

    def __init__(self, config):
        self.config = config
        with open(config.get('xero', 'private_key_file')) as keyfile:
            rsa_key = keyfile.read()

        credentials = PrivateCredentials(config.get('xero', 'consumer_key'), rsa_key)
        self.xero = Xero(credentials)
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
        existing_journals = self.xero.manualjournals.filter(since=date_cutoff, status='POSTED')
        if existing_journals is not None:
            if not isinstance(existing_journals, (list, tuple)):
                existing_journals = [existing_journals]

            for journal in existing_journals:
                result = gcid_regex.search(journal['Narration'])
                if result:
                    yield result.groups(1)[0]

    def parse_gocardless_payout(self, payout):
        """ Convert a GoCardless payout object into a JSON representation of the Xero journal XML. """
        return {
            'Narration': "GoCardless payout %s" % payout.id,
            'Status': 'POSTED',
            'Date': payout.paid_at.isoformat(),
            'LineAmountTypes': 'Inclusive',
            'JournalLines': [
                {'LineAmount': str(-(Decimal(payout.amount)) - Decimal(payout.transaction_fees)),
                 'AccountCode': self.config.get('xero', 'sales_account')},
                {'LineAmount': str(Decimal(payout.transaction_fees)),
                 'AccountCode': self.config.get('xero', 'commission_account'),
                 'TaxType': 'EXEMPTINPUT'},
                {'LineAmount': str(Decimal(payout.amount)),
                 'AccountCode': self.config.get('xero', 'gocardless_account')}
            ]}

    def get_journals_to_submit(self, posted_journals):
        for payout in self.gc_client.merchant().payouts():
            if (payout.paid_at < datetime.now() and payout.paid_at > date_cutoff
                    and payout.id not in posted_journals):
                yield self.parse_gocardless_payout(payout)

    def submit_journals(self, to_submit):
        for start in range(0, len(to_submit), 100):
            self.xero.manualjournals.put(to_submit[start:start + 100])

if __name__ == '__main__':
    config = ConfigParser()
    config.read(['xero-tools.cfg'])
    GoCardlessPaymentImporter(config).do_import()
