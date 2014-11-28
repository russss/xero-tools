#!/usr/bin/env python
import re
from decimal import Decimal
from datetime import datetime, timedelta
from ConfigParser import ConfigParser

from xero import Xero
from xero.auth import PrivateCredentials
import stripe

date_cutoff = datetime.now() - timedelta(days=300)
stripe_regex = re.compile(r'\Stripe payout (tr_[0-9A-Za-z]+)')


def unix_timestamp(dt):
    return int((dt - datetime(1970, 1, 1)).total_seconds())


def decimalise(amount):
    return Decimal(amount) / 100


class StripePaymentImporter(object):

    def __init__(self, config):
        self.config = config

        stripe.api_key = config.get('stripe', 'secret_key')
        with open(config.get('xero', 'private_key_file')) as keyfile:
            rsa_key = keyfile.read()

        credentials = PrivateCredentials(config.get('xero', 'consumer_key'), rsa_key)
        self.xero = Xero(credentials)

    def do_import(self):
        print "Importing Stripe transactions"
        posted_journals = set(self.get_posted_journals())
        to_submit = list(self.get_journals_to_submit(posted_journals))
        self.submit_journals(to_submit)
        print "Submitted %s new journal entries" % len(to_submit)
        print "NOTE: Only inserted journals in your main currency (xero limitation)."

    def get_posted_journals(self):
        existing_journals = self.xero.manualjournals.filter(since=date_cutoff, status='POSTED')
        if existing_journals is not None:
            if not isinstance(existing_journals, (list, tuple)):
                existing_journals = [existing_journals]

            for journal in existing_journals:
                result = stripe_regex.search(journal['Narration'])
                if result:
                    yield result.groups(1)[0]

    def parse_stripe_transfer(self, payout):
        """ Convert a Stripe payout object into a JSON representation of the Xero journal XML. """

        lines = [{'Description': 'Sales through Stripe',
                  'LineAmount': str(-payout['charge_gross']),
                  'AccountCode': self.config.get('xero', 'sales_account')},
                 {'Description': 'Stripe commission',
                  'LineAmount': str(payout['charge_fees']),
                  'AccountCode': self.config.get('xero', 'commission_account'),
                  'TaxType': self.config.get('xero', 'reverse_charge_tax_type')},
                 {'Description': 'Payout received from Stripe',
                  'LineAmount': str(payout['net_amount']),
                  'AccountCode': self.config.get('xero', 'stripe_account')}]

        if payout['refund_gross'] != 0:
            lines.append({'Description': 'Stripe refund',
                          'LineAmount': str(-payout['refund_gross']),
                          'AccountCode': self.config.get('xero', 'sales_account')})
            lines.append({'Description': 'Stripe commission refund',
                          'LineAmount': str(payout['refund_fees']),
                          'AccountCode': self.config.get('xero', 'commission_account'),
                          'TaxType': self.config.get('xero', 'reverse_charge_tax_type')})

        return {
            'Narration': "Stripe payout %s" % payout['id'],
            'Status': 'POSTED',
            'Date': payout['created'].isoformat(),
            'LineAmountTypes': 'Inclusive',
            'JournalLines': lines}

    def get_journals_to_submit(self, posted_journals):
        all_transfers = stripe.Transfer.all(created={'lt': unix_timestamp(datetime.now()),
                                                     'gt': unix_timestamp(date_cutoff)}, limit=100)
        for transfer in all_transfers['data']:
            result = {'id': transfer['id'],
                      'created': datetime.fromtimestamp(transfer['created']),
                      'currency': transfer['currency'],
                      'net_amount': decimalise(transfer['amount']),
                      'charge_gross': decimalise(transfer['summary']['charge_gross']),
                      'charge_fees': decimalise(transfer['summary']['charge_fees']),
                      'refund_gross': decimalise(transfer['summary']['refund_gross']),
                      'refund_fees': decimalise(transfer['summary']['refund_fees'])
                      }
            if result['created'] < datetime.now() and result['created'] > date_cutoff and\
               transfer['id'] not in posted_journals and result['currency'] == 'gbp':
                yield self.parse_stripe_transfer(result)

    def submit_journals(self, to_submit):
        for start in range(0, len(to_submit), 100):
            self.xero.manualjournals.put(to_submit[start:start + 100])

if __name__ == '__main__':
    config = ConfigParser()
    config.read(['xero-tools.cfg'])
    StripePaymentImporter(config).do_import()
