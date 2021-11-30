# -*- coding: utf-8 -*-

from odoo import api, fields, models, _
from odoo.exceptions import UserError


class PatientLabTestLine(models.Model):
    _name = "laboratory.request.line"
    _description = "Test Lines"

    test_id = fields.Many2one('ksc.lab.test',string='Test', ondelete='cascade', required=True)
    ksc_tat = fields.Char(related='test_id.ksc_tat', string='Turnaround Time', readonly=True)
    test_type = fields.Selection(related='test_id.test_type', string='Test Type', readonly=True)
    instruction = fields.Char(string='Special Instructions')
    request_id = fields.Many2one('ksc.laboratory.request',string='Lines', ondelete='cascade')
    sale_price = fields.Float(string='Sale Price')
    company_id = fields.Many2one('res.company', ondelete='restrict', 
        string='Company',related='request_id.company_id') 

    @api.onchange('test_id')
    def onchange_test(self):
        if self.test_id:
            if self.request_id.pricelist_id:
                product_id = self.test_id.product_id.with_context(pricelist=self.request_id.pricelist_id.id)
                self.sale_price = product_id.price
            else:
                self.sale_price = self.test_id.product_id.lst_price


class LaboratoryRequest(models.Model):
    _name = 'ksc.laboratory.request'
    _description = 'Laboratory Request'
    _inherit = ['portal.mixin', 'mail.thread', 'mail.activity.mixin', 'ksc.mixin']
    _order = 'date desc, id desc'

    @api.depends('line_ids')
    def _get_total_price(self):
        self.total_price = sum(line.sale_price for line in self.line_ids)

    STATES = {'requested': [('readonly', True)], 'accepted': [('readonly', True)], 'in_progress': [('readonly', True)], 'cancel': [('readonly', True)], 'done': [('readonly', True)]}

    name = fields.Char(string='Lab Request ID', readonly=True, index=True, copy=False, tracking=True)
    notes = fields.Text(string='Notes', states=STATES)
    date = fields.Datetime('Date', readonly=True, default=fields.Datetime.now, states=STATES, tracking=True)
    state = fields.Selection([
        ('draft','Draft'),
        ('requested','Requested'),
        ('accepted','Accepted'),
        ('in_progress','In Progress'),
        ('cancel','Cancel'),
        ('done','Done')],
        string='State',readonly=True, default='draft', tracking=True)
    patient_id = fields.Many2one('res.partner', domain="[('is_patient','=',True)]", string='Patient', required=True, ondelete='restrict', states=STATES, tracking=True)
    physician_id = fields.Many2one('res.partner', domain="[('is_physician','=',True)]", string='Prescribing Doctor', help="Doctor who Request the lab test.", ondelete='restrict', states=STATES, tracking=True)
    invoice_id = fields.Many2one('account.move',string='Invoice', copy=False, states=STATES)
    lab_bill_id = fields.Many2one('account.move',string='Vendor Bill', copy=False, states=STATES)
    line_ids = fields.One2many('laboratory.request.line', 'request_id',
        string='Lab Test Line', states=STATES)
    no_invoice = fields.Boolean(string='Invoice Exempt', states=STATES)
    total_price = fields.Float(compute=_get_total_price, string='Total')
    info = fields.Text(string='Extra Info', states=STATES)
    critearea_ids = fields.One2many('lab.test.critearea', 'request_id', string='Test Cases', states=STATES)
    company_id = fields.Many2one('res.company', ondelete='restrict', 
        string='Hospital', default=lambda self: self.env.user.company_id.id, states=STATES)
    pricelist_id = fields.Many2one('product.pricelist', string='Pricelist', check_company=True, 
        domain="['|', ('company_id', '=', False), ('company_id', '=', company_id)]",
        help="If you change the pricelist, related invoice will be affected.")
    test_type = fields.Selection([
        ('pathology','Pathology'),
        ('radiology','Radiology'),
    ], string='Test Type', states=STATES, default='pathology')

    LABSTATES = {'cancel': [('readonly', True)], 'done': [('readonly', True)]}

    other_laboratory = fields.Boolean(string='Send to Other Laboratory', states=LABSTATES)
    laboratory_id = fields.Many2one('ksc.laboratory', ondelete='restrict', string='Laboratory', states=LABSTATES)
    sample_ids = fields.One2many('ksc.patient.laboratory.sample', 'request_id', string='Test Samples', states=STATES)
    laboratory_group_id = fields.Many2one('laboratory.group', ondelete="set null", string='Laboratory Group', states=STATES)

    @api.onchange('laboratory_group_id')
    def onchange_laboratory_group(self):
        test_line_ids = []
        if self.laboratory_group_id:
            for line in self.laboratory_group_id.line_ids:
                test_line_ids.append((0,0,{
                    'test_id': line.test_id.id,
                    'instruction': line.instruction,
                    'test_type' : line.test_type,
                    'sale_price' : line.sale_price,
                }))
            self.line_ids = test_line_ids

    def unlink(self):
        for rec in self:
            if rec.state not in ['draft']:
                raise UserError(_("Lab Requests can be delete only in Draft state."))
        return super(LaboratoryRequest, self).unlink()

    def button_requested(self):
        if not self.line_ids:
            raise UserError(_('Please add atleast one Laboratory test line before submiting request.'))
        self.name = self.env['ir.sequence'].next_by_code('ksc.laboratory.request')
        self.date = fields.Datetime.now()
        self.state = 'requested'

    def button_accept(self):
        Sample = self.env['ksc.patient.laboratory.sample']
        if self.sudo().company_id.ksc_auto_create_lab_sample:
            for line in self.line_ids:
                if line.test_id.sample_type_id:
                    Sample.create({
                        'sample_type_id': line.test_id.sample_type_id.id,
                        'request_id': line.request_id.id,
                        'user_id': self.env.user.id,
                        'company_id': line.request_id.sudo().company_id.id,
                    })
        self.state = 'accepted'

    def prepare_test_result_data(self, line):
        res = {
            'patient_id': self.patient_id.id,
            'physician_id': self.physician_id and self.physician_id.id,
            'test_id': line.test_id.id,
            'user_id': self.env.user.id,
            'date_analysis': self.date,
            'request_id': self.id,
        }
        return res

    def button_in_progress(self):
        self.state = 'in_progress'
        Critearea = self.env['lab.test.critearea']
        LabTest = self.env['patient.laboratory.test']
        # Consumable = self.env['hms.consumable.line']
        gender = self.patient_id.gender
        for line in self.line_ids:
            lab_test_data = self.prepare_test_result_data(line)
            test_result = LabTest.create(lab_test_data)
            for res_line in line.test_id.critearea_ids:
                Critearea.create({
                    'patient_lab_id': test_result.id,
                    'name': res_line.name,
                    'normal_range': res_line.normal_range_female if gender=='female' else res_line.normal_range_male,
                    'lab_uom_id': res_line.lab_uom_id and res_line.lab_uom_id.id or False,
                    'sequence': res_line.sequence,
                    'remark': res_line.remark,
                    'display_type': res_line.display_type,
                })

            # for con_line in line.test_id.consumable_line_ids:
            #     Consumable.create({
            #         'patient_lab_test_id': test_result.id,
            #         'name': con_line.name,
            #         'product_id': con_line.product_id and con_line.product_id.id or False,
            #         'product_uom': con_line.product_uom and con_line.product_uom.id or False,
            #         'qty': con_line.qty,
            #         'date': fields.Date.today(),
            #     })

    def button_done(self):
        self.state = 'done'

    def button_cancel(self):
        self.state = 'cancel'

    def create_invoice(self):
        if not self.line_ids:
            raise UserError(_("Please add lab Tests first."))

        product_data = []
        for line in self.line_ids:
            product_data.append({
                'product_id': line.test_id.product_id,
                'price_unit': line.sale_price,
            })
        pricelist_context = {}
        if self.pricelist_id:
            pricelist_context = {'ksc_pricelist_id': self.pricelist_id.id}
        invoice = self.ksc_create_invoice(partner=self.patient_id,product_data=product_data)
        self.invoice_id = invoice.id
        invoice.request_id = self.id

    def create_laboratory_bill(self):
        if not self.line_ids:
            raise UserError(_("Please add lab Tests first."))

        product_data = []
        for line in self.line_ids:
            product_data.append({
                'product_id': line.test_id.product_id,
                'price_unit': line.test_id.product_id.standard_price,
            })

        inv_data={'type': 'in_invoice'}
        bill = self.ksc_create_invoice(partner=self.laboratory_id.partner_id, product_data=product_data, inv_data=inv_data)
        self.lab_bill_id = bill.id
        bill.request_id = self.id

    def view_invoice(self):
        invoices = self.mapped('invoice_id')
        action = self.ksc_action_view_invoice(invoices)
        return action

    def action_view_test_results(self):
        action = self.env["ir.actions.actions"]._for_xml_id("ksc_laboratory.action_lab_result")
        action['domain'] = [('request_id','=',self.id)]
        action['context'] = {'default_request_id': self.id, 'default_physician_id': self.physician_id.id}
        return action

    def action_view_lab_samples(self):
        action = self.env["ir.actions.actions"]._for_xml_id("ksc_laboratory.action_ksc_patient_laboratory_sample")
        action['domain'] = [('request_id','=',self.id)]
        action['context'] = {'default_request_id': self.id}
        return action
    
    def action_sendmail(self):
        '''
        This function opens a window to compose an email, with the template message loaded by default
        '''
        self.ensure_one()
        ir_model_data = self.env['ir.model.data']
        try:
            template_id = ir_model_data.get_object_reference('ksc_laboratory', 'ksc_lab_req_email')[1]
        except ValueError:
            template_id = False
        try:
            compose_form_id = ir_model_data.get_object_reference('mail', 'email_compose_message_wizard_form')[1]
        except ValueError:
            compose_form_id = False
        ctx = {
            'default_model': 'ksc.laboratory.request',
            'default_res_id': self.ids[0],
            'default_use_template': bool(template_id),
            'default_template_id': template_id,
            'default_composition_mode': 'comment',
            'force_email': True
        }
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'mail.compose.message',
            'views': [(compose_form_id, 'form')],
            'view_id': compose_form_id,
            'target': 'new',
            'context': ctx,
        }

# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4: