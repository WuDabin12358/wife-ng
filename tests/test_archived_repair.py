import copy
import unittest
from apply_saved_revision import validate_repair_scope


class ArchivedRepairTests(unittest.TestCase):
    def setUp(self):
        self.task={'state':'FAILED','failureCode':'BLUEPRINT_VERIFICATION_FAILED',
                   'result':{'verification_complete':True,'mismatch_count':1,
                             'mismatches':[{'at':[25,4,13],'actual':'minecraft:dirt'}]}}
        self.plan={'operations':[{'from':[25,4,13],'to':[25,4,13],'state':'minecraft:coarse_dirt'}]}

    def test_only_explicitly_revised_cells_can_be_repaired(self):
        validate_repair_scope(self.task,self.plan)
        other=copy.deepcopy(self.task)
        other['result']['mismatches'][0]['at']=[26,4,13]
        with self.assertRaises(RuntimeError): validate_repair_scope(other,self.plan)

    def test_incomplete_or_truncated_verification_cannot_authorize_repair(self):
        for field,value in [('verification_complete',False),('mismatch_count',2)]:
            other=copy.deepcopy(self.task)
            other['result'][field]=value
            with self.assertRaises(RuntimeError): validate_repair_scope(other,self.plan)

    def test_unknown_world_cells_cannot_authorize_repair(self):
        other=copy.deepcopy(self.task)
        other['result']['mismatches'][0]['actual']='__unloaded__'
        with self.assertRaises(RuntimeError): validate_repair_scope(other,self.plan)

    def test_completed_verification_can_report_unmatched_geometry(self):
        other=copy.deepcopy(self.task)
        other['state']='SUCCEEDED'
        other['failureCode']=''
        other['result']['verified']=False
        validate_repair_scope(other,self.plan)
