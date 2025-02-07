import unittest

import frappe
from frappe.utils import add_days, add_months, getdate, nowdate

import erpnext
from erpnext.hr.doctype.employee.test_employee import make_employee
from erpnext.hr.doctype.leave_ledger_entry.leave_ledger_entry import process_expired_allocation
from erpnext.hr.doctype.leave_type.test_leave_type import create_leave_type


class TestLeaveAllocation(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		frappe.db.sql("delete from `tabLeave Period`")

		emp_id = make_employee("test_emp_leave_allocation@salary.com")
		cls.employee = frappe.get_doc("Employee", emp_id)

	def tearDown(self):
		frappe.db.rollback()

	def test_overlapping_allocation(self):
		leaves = [
			{
				"doctype": "Leave Allocation",
				"__islocal": 1,
				"employee": self.employee.name,
				"employee_name": self.employee.employee_name,
				"leave_type": "_Test Leave Type",
				"from_date": getdate("2015-10-01"),
				"to_date": getdate("2015-10-31"),
				"new_leaves_allocated": 5,
				"docstatus": 1,
			},
			{
				"doctype": "Leave Allocation",
				"__islocal": 1,
				"employee": self.employee.name,
				"employee_name": self.employee.employee_name,
				"leave_type": "_Test Leave Type",
				"from_date": getdate("2015-09-01"),
				"to_date": getdate("2015-11-30"),
				"new_leaves_allocated": 5,
			},
		]

		frappe.get_doc(leaves[0]).save()
		self.assertRaises(frappe.ValidationError, frappe.get_doc(leaves[1]).save)

	def test_invalid_period(self):
		doc = frappe.get_doc(
			{
				"doctype": "Leave Allocation",
				"__islocal": 1,
				"employee": self.employee.name,
				"employee_name": self.employee.employee_name,
				"leave_type": "_Test Leave Type",
				"from_date": getdate("2015-09-30"),
				"to_date": getdate("2015-09-1"),
				"new_leaves_allocated": 5,
			}
		)

		# invalid period
		self.assertRaises(frappe.ValidationError, doc.save)

	def test_allocated_leave_days_over_period(self):
		doc = frappe.get_doc(
			{
				"doctype": "Leave Allocation",
				"__islocal": 1,
				"employee": self.employee.name,
				"employee_name": self.employee.employee_name,
				"leave_type": "_Test Leave Type",
				"from_date": getdate("2015-09-1"),
				"to_date": getdate("2015-09-30"),
				"new_leaves_allocated": 35,
			}
		)

		# allocated leave more than period
		self.assertRaises(frappe.ValidationError, doc.save)

	def test_carry_forward_calculation(self):
		leave_type = create_leave_type(leave_type_name="_Test_CF_leave", is_carry_forward=1)
		leave_type.maximum_carry_forwarded_leaves = 10
		leave_type.max_leaves_allowed = 30
		leave_type.save()

		# initial leave allocation = 15
		leave_allocation = create_leave_allocation(
			employee=self.employee.name,
			employee_name=self.employee.employee_name,
			leave_type="_Test_CF_leave",
			from_date=add_months(nowdate(), -12),
			to_date=add_months(nowdate(), -1),
			carry_forward=0,
		)
		leave_allocation.submit()

		# carry forwarded leaves considering maximum_carry_forwarded_leaves
		# new_leaves = 15, carry_forwarded = 10
		leave_allocation_1 = create_leave_allocation(
			employee=self.employee.name,
			employee_name=self.employee.employee_name,
			leave_type="_Test_CF_leave",
			carry_forward=1,
		)
		leave_allocation_1.submit()

		self.assertEqual(leave_allocation_1.unused_leaves, 10)

		leave_allocation_1.cancel()

		# carry forwarded leaves considering max_leave_allowed
		# max_leave_allowed = 30, new_leaves = 25, carry_forwarded = 5
		leave_allocation_2 = create_leave_allocation(
			employee=self.employee.name,
			employee_name=self.employee.employee_name,
			leave_type="_Test_CF_leave",
			carry_forward=1,
			new_leaves_allocated=25,
		)
		leave_allocation_2.submit()

		self.assertEqual(leave_allocation_2.unused_leaves, 5)

	def test_carry_forward_leaves_expiry(self):
		leave_type = create_leave_type(
			leave_type_name="_Test_CF_leave_expiry",
			is_carry_forward=1,
			expire_carry_forwarded_leaves_after_days=90,
		)
		leave_type.save()

		# initial leave allocation
		leave_allocation = create_leave_allocation(
			employee=self.employee.name,
			employee_name=self.employee.employee_name,
			leave_type="_Test_CF_leave_expiry",
			from_date=add_months(nowdate(), -24),
			to_date=add_months(nowdate(), -12),
			carry_forward=0,
		)
		leave_allocation.submit()

		leave_allocation = create_leave_allocation(
			employee=self.employee.name,
			employee_name=self.employee.employee_name,
			leave_type="_Test_CF_leave_expiry",
			from_date=add_days(nowdate(), -90),
			to_date=add_days(nowdate(), 100),
			carry_forward=1,
		)
		leave_allocation.submit()

		# expires all the carry forwarded leaves after 90 days
		process_expired_allocation()

		# leave allocation with carry forward of only new leaves allocated
		leave_allocation_1 = create_leave_allocation(
			employee=self.employee.name,
			employee_name=self.employee.employee_name,
			leave_type="_Test_CF_leave_expiry",
			carry_forward=1,
			from_date=add_months(nowdate(), 6),
			to_date=add_months(nowdate(), 12),
		)
		leave_allocation_1.submit()

		self.assertEqual(leave_allocation_1.unused_leaves, leave_allocation.new_leaves_allocated)

	def test_creation_of_leave_ledger_entry_on_submit(self):
		leave_allocation = create_leave_allocation(
			employee=self.employee.name, employee_name=self.employee.employee_name
		)
		leave_allocation.submit()

		leave_ledger_entry = frappe.get_all(
			"Leave Ledger Entry", fields="*", filters=dict(transaction_name=leave_allocation.name)
		)

		self.assertEqual(len(leave_ledger_entry), 1)
		self.assertEqual(leave_ledger_entry[0].employee, leave_allocation.employee)
		self.assertEqual(leave_ledger_entry[0].leave_type, leave_allocation.leave_type)
		self.assertEqual(leave_ledger_entry[0].leaves, leave_allocation.new_leaves_allocated)

		# check if leave ledger entry is deleted on cancellation
		leave_allocation.cancel()
		self.assertFalse(
			frappe.db.exists("Leave Ledger Entry", {"transaction_name": leave_allocation.name})
		)

	def test_leave_addition_after_submit(self):
		leave_allocation = create_leave_allocation(
			employee=self.employee.name, employee_name=self.employee.employee_name
		)
		leave_allocation.submit()
		self.assertTrue(leave_allocation.total_leaves_allocated, 15)
		leave_allocation.new_leaves_allocated = 40
		leave_allocation.submit()
		self.assertTrue(leave_allocation.total_leaves_allocated, 40)

	def test_leave_subtraction_after_submit(self):
		leave_allocation = create_leave_allocation(
			employee=self.employee.name, employee_name=self.employee.employee_name
		)
		leave_allocation.submit()
		self.assertTrue(leave_allocation.total_leaves_allocated, 15)
		leave_allocation.new_leaves_allocated = 10
		leave_allocation.submit()
		self.assertTrue(leave_allocation.total_leaves_allocated, 10)

	def test_validation_against_leave_application_after_submit(self):
		from erpnext.payroll.doctype.salary_slip.test_salary_slip import make_holiday_list

		make_holiday_list()
		frappe.db.set_value(
			"Company", self.employee.company, "default_holiday_list", "Salary Slip Test Holiday List"
		)

		leave_allocation = create_leave_allocation(
			employee=self.employee.name, employee_name=self.employee.employee_name
		)
		leave_allocation.submit()
		self.assertTrue(leave_allocation.total_leaves_allocated, 15)

		leave_application = frappe.get_doc(
			{
				"doctype": "Leave Application",
				"employee": self.employee.name,
				"leave_type": "_Test Leave Type",
				"from_date": add_months(nowdate(), 2),
				"to_date": add_months(add_days(nowdate(), 10), 2),
				"company": self.employee.company,
				"docstatus": 1,
				"status": "Approved",
				"leave_approver": "test@example.com",
			}
		)
		leave_application.submit()
		leave_application.reload()

		# allocate less leaves than the ones which are already approved
		leave_allocation.new_leaves_allocated = leave_application.total_leave_days - 1
		leave_allocation.total_leaves_allocated = leave_application.total_leave_days - 1
		self.assertRaises(frappe.ValidationError, leave_allocation.submit)


def create_leave_allocation(**args):
	args = frappe._dict(args)

	emp_id = make_employee("test_emp_leave_allocation@salary.com")
	employee = frappe.get_doc("Employee", emp_id)

	return frappe.get_doc(
		{
			"doctype": "Leave Allocation",
			"__islocal": 1,
			"employee": args.employee or employee.name,
			"employee_name": args.employee_name or employee.employee_name,
			"leave_type": args.leave_type or "_Test Leave Type",
			"from_date": args.from_date or nowdate(),
			"new_leaves_allocated": args.new_leaves_allocated or 15,
			"carry_forward": args.carry_forward or 0,
			"to_date": args.to_date or add_months(nowdate(), 12),
		}
	)


test_dependencies = ["Employee", "Leave Type"]
