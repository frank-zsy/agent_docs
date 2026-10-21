"""Local-only security probes; asserts current vulnerable behavior, not fixes.
No external requests: payment, tier, and contribution providers are stubbed.
Run with Django's test runner and an isolated SQLite database.
"""
from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth.models import Permission
from django.db import transaction
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from social_django.models import UserSocialAuth

from accounts.models import User, UserProfile, Education, AccountMergeRequest
from accounts.services import profile_completion_reward as rewards
from accounts.services.account_merge import perform_merge
from accounts.services.jwt_tokens import create_access_token
from points import services as points
from points.models import PointSource, PointType, WithdrawalRequest, WithdrawalStatus, Tag
from shenbianyun import services as payments
from shenbianyun.models import PaymentRecord, PaymentState
from shop.models import ShopItem, Redemption
from shop.pricing import UserTierDiscount
from shop.services import redeem_item, RedemptionError
from talent_reach import services as outreach
from talent_reach.models import OutreachRecipient


@override_settings(SBY_TASK_ID='1', SBY_PROVIDER_ID='1',
                   OUTREACH_COST_PER_USER=2, OUTREACH_REWARD_RATIO=0.5,
                   OUTREACH_REWARD_EXPIRY_DAYS=30)
class SecurityProbes(TestCase):
    def user(self, name, staff=False, complete=False):
        u = User.objects.create_user(username=name, email=f'{name}@audit.invalid', is_active=True, is_staff=staff)
        p = UserProfile.objects.create(user=u, location_country_id=':divisions/CN', birth_date=date.today()-timedelta(days=20*365))
        if complete:
            Education.objects.create(profile=p, institution_name='Audit', field_of_study='CS', start_date=date(2020, 1, 1))
        return u

    def item(self, cost=100):
        return ShopItem.objects.create(name_zh='Audit item', name_en='Audit item', description_zh='Audit', cost=cost, stock=10)

    def approved(self, name, amount=200):
        u = self.user(name)
        points.grant_points(u, amount, PointType.CASH, 'test fixture')
        wr = points.create_withdrawal_request(u, amount, real_name='Audit', bank_account='000000', phone='13000000000', id_card='000000000000000000')
        points.approve_withdrawal(wr.id, self.user(f'{name}_reviewer', staff=True))
        return u, wr

    def record(self, wr, order, state=PaymentState.INITIATED, batch='audit-batch'):
        return PaymentRecord.objects.create(mer_batch_id=batch, mer_order_id=order, order_no=f'provider-{order}', withdrawal_request=wr, amount=wr.amount*10, state=state, payee_name='Audit', payee_acc='000000', id_card='000000000000000000', mobile='13000000000')

    def emit(self, label, **result):
        print(f'PROBE {label}: {result}', flush=True)

    def test_01_staff_without_grant_permission_can_mint_cash(self):
        u = self.user('limited_staff', staff=True)
        self.assertFalse(u.has_perm('points.add_pointsource'))
        self.assertFalse(u.is_superuser)
        c = Client()
        c.force_login(u)
        response = c.post(f'/admin/points/grant-to-users/?ids={u.pk}', {'point_type': 'cash', 'amount': 100000, 'reason': 'audit'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(points.get_balance(u, PointType.CASH), 100000)
        self.emit('staff_mint', permissions=list(u.get_all_permissions()), response=response.status_code, balance=100000)

    def test_02_reward_race_controlled_interleaving_through_api(self):
        u = self.user('reward_race', complete=True)
        headers = {'HTTP_AUTHORIZATION': f'Bearer {create_access_token(u)}', 'content_type': 'application/json'}
        nested_response = []
        calls = 0
        def external_tier_lookup(_user):
            nonlocal calls
            calls += 1
            if calls == 1:
                # Pause request A after its real has_claimed_reward returned False;
                # request B now completes before A resumes. No guards are mocked.
                nested_response.append(Client().patch('/api/v1/me/profile', {'bio': 'B'}, **headers))
            return {'points': 200, 'highest_level': 'SSS', 'highest_level_year': 2025}
        with patch.object(rewards, 'calculate_reward_points', side_effect=external_tier_lookup):
            response = Client().patch('/api/v1/me/profile', {'bio': 'A'}, **headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(nested_response[0].status_code, 200)
        count = PointSource.objects.filter(reference_id=f'profile_completion_reward:{u.pk}').count()
        self.assertEqual(count, 2)
        self.assertEqual(points.get_balance(u), 400)
        self.emit('reward_interleaving', responses=[response.status_code,nested_response[0].status_code], sources=count, balance=400)

    def test_03_polling_one_batch_repeats_failed_refund(self):
        u, failed_wr = self.approved('failed')
        _, pending_wr = self.approved('pending')
        failed = self.record(failed_wr, 'failed')
        pending = self.record(pending_wr, 'pending')
        response = {'res_data': {'queryItems': [
            {'orderNo': failed.order_no, 'state': int(PaymentState.FAILED), 'resMsg': 'declined'},
            {'orderNo': pending.order_no, 'state': int(PaymentState.PAYING)},
        ]}}
        with patch.object(payments.ShenbianyunClient, 'request', return_value=response):
            payments.check_payment_status()
            balance_first = points.get_balance(u)
            payments.check_payment_status()
        self.assertEqual(balance_first, 200)
        self.assertEqual(points.get_balance(u), 400)
        self.assertEqual(PointSource.objects.filter(reference_id=f'refund:withdrawal:{failed_wr.pk}').count(), 2)
        self.emit('batch_refund_replay', first_balance=balance_first, second_balance=400)

    def test_04_accepted_payment_with_lost_response_is_refunded(self):
        u, wr = self.approved('timeout')
        accepted = []
        def provider_accepted_then_timed_out(*args, **kwargs):
            accepted.extend(kwargs['req_data']['payItems'])
            raise payments.ShenbianyunRequestError('simulated response timeout after provider acceptance')
        with patch.object(payments.ShenbianyunClient, 'request', side_effect=provider_accepted_then_timed_out):
            result = payments.batch_payment()
        wr.refresh_from_db()
        record = PaymentRecord.objects.get(withdrawal_request=wr)
        self.assertEqual(len(accepted), 1)
        self.assertEqual(points.get_balance(u), 200)
        self.assertEqual(record.state, PaymentState.FAILED)
        self.assertEqual(payments.check_payment_status()['checked'], 0)
        self.emit('ambiguous_payment_refund', simulated_accepted_orders=len(accepted), balance=200, withdrawal_status=wr.status, next_poll_checked=0)

    def test_05_expired_points_can_redeem(self):
        u = self.user('expired')
        points.grant_points(u, 100, PointType.GIFT, 'expired fixture', expires_at=timezone.now()-timedelta(days=1))
        with patch('shop.services.resolve_user_tier_discount', return_value=UserTierDiscount()):
            result = redeem_item(u, self.item().pk)
        self.assertEqual(result['redemption'].status, Redemption.StatusChoices.COMPLETED)
        self.emit('expired_redemption', status=result['redemption'].status, balance=points.get_balance(u))

    def test_06_outreach_removes_restricted_tag(self):
        u = self.user('tagged_sender')
        recipient = self.user('tagged_receiver')
        tag = Tag.objects.create(name='Restricted Audit', slug='restricted-audit')
        points.grant_points(u, 2, PointType.GIFT, 'tagged fixture', tag_slug=tag.slug)
        draft = outreach.create_draft(author=u, title='Audit', content='Audit')
        preview = {'developers': [{'user_id': recipient.pk, 'openrank_score': 1.0}]}
        with patch.object(outreach, 'preview_recipients', return_value=preview):
            campaign = outreach.send_outreach(draft.id, u, ['audit'], ['audit'], None, None, None, 1, PointType.GIFT, tag_slug=tag.slug)
        grant_recipient = OutreachRecipient.objects.get(campaign=campaign, user=recipient)
        result = outreach.claim_reading_reward(recipient, grant_recipient.user_message_id)
        source = PointSource.objects.get(wallet=points.get_or_create_wallet(recipient))
        self.assertIsNone(source.tag_id)
        with patch('shop.services.resolve_user_tier_discount', return_value=UserTierDiscount()):
            redeemed = redeem_item(recipient, self.item(cost=1).pk)
        self.assertEqual(redeemed['redemption'].status, Redemption.StatusChoices.COMPLETED)
        self.emit('tag_laundering', source_tag=campaign.tag_slug, granted_tag=source.tag_id, reward=result['reward_amount'], redemption='completed')

    def test_07_admin_status_edit_bypasses_debit(self):
        u = self.user('status_owner')
        points.grant_points(u, 200, PointType.CASH, 'fixture')
        wr = points.create_withdrawal_request(u, 200)
        staff = self.user('status_staff', staff=True)
        staff.user_permissions.add(Permission.objects.get(codename='change_withdrawalrequest'))
        c = Client()
        c.force_login(staff)
        response = c.post(f'/admin/points/withdrawalrequest/{wr.pk}/change/', {'status': 'approved', 'admin_note': 'Audit', '_save': 'Save'})
        self.assertEqual(response.status_code, 302, response.content[:2000])
        wr.refresh_from_db()
        self.assertEqual(wr.status, WithdrawalStatus.APPROVED)
        self.assertIsNone(wr.transaction_id)
        with patch.object(payments.ShenbianyunClient, 'request', return_value={'res_data': {}}) as provider:
            payments.batch_payment()
        self.assertEqual(provider.call_count, 1)
        self.assertEqual(points.get_balance(u), 200)
        self.emit('admin_status_bypass', status=wr.status, debit_transaction=wr.transaction_id, balance=200, submitted_payments=1)

    def test_08_batch_selection_race_creates_two_payments(self):
        u, wr = self.approved('double_pay')
        real_create = PaymentRecord.objects.create
        nested = False
        submitted = []
        def provider(*args, **kwargs):
            submitted.extend(kwargs['req_data']['payItems'])
            return {'res_data': {}}
        def create_with_interleaving(**kwargs):
            nonlocal nested
            if not nested:
                nested = True
                # Worker A has selected wr but not created its payment record.
                # Worker B selects and submits wr before worker A resumes.
                payments.batch_payment()
            return real_create(**kwargs)
        with patch.object(payments.ShenbianyunClient, 'request', side_effect=provider), patch.object(PaymentRecord.objects, 'create', side_effect=create_with_interleaving):
            payments.batch_payment()
        self.assertEqual(PaymentRecord.objects.filter(withdrawal_request=wr).count(), 2)
        self.assertEqual(len(submitted), 2)
        self.assertNotEqual(submitted[0]['merOrderId'], submitted[1]['merOrderId'])
        self.emit('payment_selection_interleaving', debited_points=200, payment_records=2, submitted_orders=2)

    def test_09_merge_allows_same_social_identity_reward_again(self):
        source = self.user('merge_source', complete=True)
        target = self.user('merge_target', complete=True)
        UserSocialAuth.objects.create(user=source, provider='github', uid='audit-identity')
        # The external contribution oracle always describes the same verified UID.
        with patch.object(rewards, 'query_user_yearly_openrank', return_value=[{'year': 2025, 'yearly_openrank': 1000}]), patch.object(rewards, '_fetch_baseline_tiers', return_value={'2025': [100,90,80,70,60,50]}):
            first = rewards.grant_profile_completion_reward(source)
            with patch('shop.services.resolve_user_tier_discount', return_value=UserTierDiscount()):
                redeem_item(source, self.item(cost=200).pk)
            req = AccountMergeRequest.objects.create(source_user=source, target_user=target, status=AccountMergeRequest.Status.PENDING, approve_token='audit-merge', expires_at=timezone.now()+timedelta(days=1))
            perform_merge(req)
            target = User.objects.get(pk=target.pk)
            self.assertFalse(rewards.has_claimed_reward(target))
            second = rewards.grant_profile_completion_reward(target)
        self.assertEqual(first['points'], 200)
        self.assertEqual(second['points'], 200)
        self.assertEqual(points.get_balance(target), 200)
        self.emit('merge_reward_reuse', same_social_uid='audit-identity', first_reward=200, first_redeemed=200, second_reward=200)

    def allocation_payload(self, receiver, amount=100, selector=None):
        return {
            'source_selector': selector or {'owner_type': 'user', 'point_type': 'gift', 'tag_slug': None},
            'project_scope': {'tags': ['audit-project'], 'operation': 'AND'},
            'start_month': '2025-01-01', 'end_month': '2025-02-01',
            'total_amount': amount,
            'allocations': [{'actor_id': 'audit-recipient', 'actor_login': receiver.username, 'platform': 'github', 'is_registered': True, 'user_id': receiver.pk, 'contribution_score': 1, 'amount': amount}],
        }

    def test_10_allocation_discards_expiry(self):
        sender = self.user('expiry_sender')
        receiver = self.user('expiry_receiver')
        source = points.grant_points(sender, 100, PointType.GIFT, 'finite grant', expires_at=timezone.now()+timedelta(days=1))
        response = Client().post('/api/v1/points/allocations', self.allocation_payload(receiver), content_type='application/json', HTTP_AUTHORIZATION=f'Bearer {create_access_token(sender)}')
        self.assertEqual(response.status_code, 201, response.content)
        received = PointSource.objects.get(wallet=points.get_or_create_wallet(receiver))
        self.assertIsNone(received.expires_at)
        self.emit('allocation_expiry_removed', input_has_expiry=source.expires_at is not None, output_expiry=received.expires_at, amount=received.remaining_amount)

    def test_11_control_unauthorized_and_invalid_allocations_fail(self):
        from accounts.models import Organization, OrganizationMembership
        sender = self.user('allocation_sender')
        receiver = self.user('allocation_receiver')
        points.grant_points(sender, 100, PointType.GIFT, 'fixture')
        headers = {'content_type': 'application/json', 'HTTP_AUTHORIZATION': f'Bearer {create_access_token(sender)}'}
        payload = self.allocation_payload(receiver)
        payload['allocations'][0]['amount'] = -100
        response_negative = Client().post('/api/v1/points/allocations', payload, **headers)
        self.assertEqual(response_negative.status_code, 422)
        payload = self.allocation_payload(receiver, amount=101)
        response_over = Client().post('/api/v1/points/allocations', payload, **headers)
        self.assertEqual(response_over.status_code, 409)
        org = Organization.objects.create(name='Audit org', slug='audit-org')
        OrganizationMembership.objects.create(user=sender, organization=org, role='member')
        points.grant_points(org, 100, PointType.GIFT, 'org fixture')
        selector = {'owner_type': 'organization', 'owner_slug': org.slug, 'point_type': 'gift', 'tag_slug': None}
        response_org = Client().post('/api/v1/points/allocations', self.allocation_payload(receiver, selector=selector), **headers)
        self.assertEqual(response_org.status_code, 403)
        self.assertEqual(points.get_balance(sender), 100)
        self.assertEqual(points.get_balance(receiver), 0)
        self.assertEqual(points.get_balance(org), 100)
        self.emit('controls_allocation', negative=response_negative.status_code, over_balance=response_over.status_code, org_member=response_org.status_code, receiver_balance=0)

    def test_12_control_tag_balance_stock_and_price_checks(self):
        u = self.user('redemption_controls')
        tag = Tag.objects.create(name='Control restricted', slug='control-restricted')
        points.grant_points(u, 100, PointType.GIFT, 'fixture', tag_slug=tag.slug)
        item = self.item(cost=100)
        with patch('shop.services.resolve_user_tier_discount', return_value=UserTierDiscount()):
            with self.assertRaises(RedemptionError):
                redeem_item(u, item.pk, tag_slug=tag.slug)
            with self.assertRaises(RedemptionError):
                redeem_item(u, item.pk)
            item.allowed_tags.add(tag)
            with self.assertRaises(RedemptionError):
                redeem_item(u, item.pk, tag_slug=tag.slug, expected_points_cost=1)
            item.stock=0
            item.save(update_fields=['stock'])
            with self.assertRaises(RedemptionError):
                redeem_item(u, item.pk, tag_slug=tag.slug)
        self.assertEqual(points.get_balance(u), 100)
        self.assertFalse(Redemption.objects.filter(user_profile=u).exists())
        self.emit('controls_redemption', mismatched_tag='blocked', wrong_bucket='blocked', altered_price='blocked', zero_stock='blocked', balance=100)
