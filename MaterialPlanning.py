import numpy as np
import urllib.request, json, time, os, copy
from scipy.optimize import linprog

global penguin_url
penguin_url = 'https://penguin-stats.io/PenguinStats/api/'

class MaterialPlanning(object):
    
    def __init__(self, 
                 filter_freq=20,
                 filter_stages=[],
                 url_stats='result/matrix?show_stage_details=true&show_item_details=true',
                 url_rules='formula', 
                 path_stats='data/matrix.json', 
                 path_rules='data/formula.json'):
        """
        Object initialization.
        Args:
            filter_freq: int or None. The lowest frequence that we consider.
                No filter will be applied if None.
            url_stats: string. url to the dropping rate stats data.
            url_rules: string. url to the composing rules data.
            path_stats: string. local path to the dropping rate stats data.
            path_rules: string. local path to the composing rules data.
        """
        try:
            material_probs, convertion_rules = load_data(path_stats, path_rules)
        except:
            print('Requesting data from web resources (i.e., penguin-stats.io)...', end=' ')
            material_probs, convertion_rules = request_data(penguin_url+url_stats, penguin_url+url_rules, path_stats, path_rules)
            print('done.')

        if filter_freq:
            filtered_probs = []
            for dct in material_probs['matrix']:
                if dct['times']>=filter_freq and dct['stage']['code'] not in filter_stages:
                    filtered_probs.append(dct)
            material_probs['matrix'] = filtered_probs

        self._set_lp_parameters(*self._pre_processing(material_probs, convertion_rules))
            
                
    def _pre_processing(self, material_probs, convertion_rules):
        """
        Compute costs, convertion rules and items probabilities from requested dictionaries.
        Args:
            material_probs: List of dictionaries recording the dropping info per stage per item.
                Keys of instances: ["itemID", "times", "itemName", "quantity", "apCost", "stageCode", "stageID"].
            convertion_rules: List of dictionaries recording the rules of composing.
                Keys of instances: ["id", "name", "level", "source", "madeof"].
        """
        # To count items and stages.
        additional_items = {'30135': u'D32钢', '30125': u'双极纳米片', '30115': u'聚合剂'}

        item_dct = {}
        stage_dct = {}
        for dct in material_probs['matrix']:
            item_dct[dct['item']['itemId']]=dct['item']['name']
            stage_dct[dct['stage']['code']]=dct['stage']['code']
        item_dct.update(additional_items)
        
        # To construct mapping from id to item names.
        item_array = []
        item_id_array = []
        for k,v in item_dct.items():
            try:
                float(k)
                item_array.append(v)
                item_id_array.append(k)
            except:
                pass
        self.item_array = np.array(item_array)
        self.item_id_array = np.array(item_id_array)
        self.item_dct_rv = {v:k for k,v in enumerate(item_array)}

        # To construct mapping from stage id to stage names and vice versa.
        stage_array = []
        for k,v in stage_dct.items():
            stage_array.append(v)
        self.stage_array = np.array(stage_array)
        self.stage_dct_rv = {v:k for k,v in enumerate(self.stage_array)}
        
        # To format dropping records into sparse probability matrix
        probs_matrix = np.zeros([len(stage_array), len(item_array)])
        cost_lst = np.zeros(len(stage_array))
        for dct in material_probs['matrix']:
            try:
                float(dct['item']['itemId'])
                probs_matrix[self.stage_dct_rv[dct['stage']['code']], self.item_dct_rv[dct['item']['name']]] = dct['quantity']/float(dct['times'])
                cost_lst[self.stage_dct_rv[dct['stage']['code']]] = dct['stage']['apCost']
            except:
                pass
        cost_lst[self.stage_dct_rv['S4-6']] -= 3228 * 0.004
                
        # To build equavalence relationship from convert_rule_dct.
        self.convertions_dct = {}
        convertion_matrix = []
        convertion_outc_matrix = []
        convertion_cost_lst = []
        for rule in convertion_rules:
            convertion = np.zeros(len(self.item_array))
            convertion[self.item_dct_rv[rule['name']]] = 1

            comp_dct = {comp['name']:comp['count'] for comp in rule['costs']}
            self.convertions_dct[rule['name']] = comp_dct
            for iname in comp_dct:
                convertion[self.item_dct_rv[iname]] -= comp_dct[iname]
            convertion_matrix.append(copy.deepcopy(convertion))

            outc_dct = {outc['name']:outc['count'] for outc in rule['extraOutcome']}
            outc_wgh = {outc['name']:outc['weight'] for outc in rule['extraOutcome']}
            weight_sum = float(sum(outc_wgh.values()))
            for iname in outc_dct:
                convertion[self.item_dct_rv[iname]] += outc_dct[iname]*0.175*outc_wgh[iname]/weight_sum
            convertion_outc_matrix.append(convertion)
            
            convertion_cost_lst.append(rule['goldCost']*0.004)

        convertion_matrix = np.array(convertion_matrix)
        convertion_outc_matrix = np.array(convertion_outc_matrix)
        convertion_cost_lst = np.array(convertion_cost_lst)
                
        return convertion_matrix, convertion_outc_matrix, convertion_cost_lst, probs_matrix, cost_lst
    
        
    def _set_lp_parameters(self, convertion_matrix, 
                           convertion_outc_matrix, 
                           convertion_cost_lst, 
                           probs_matrix, cost_lst):
        """
        Object initialization.
        Args:
            convertion_matrix: matrix of shape [n_rules, n_items]. 
                Each row represent a rule.
            convertion_cost_lst: list. Cost in equal value to the currency spent in convertion.
            probs_matrix: sparse matrix of shape [n_stages, n_items]. 
                Items per clear (probabilities) at each stage.
            cost_lst: list. Costs per clear at each stage.
        """
        self.convertion_matrix = convertion_matrix
        self.convertion_outc_matrix = convertion_outc_matrix
        self.convertion_cost_lst = convertion_cost_lst
        self.probs_matrix = probs_matrix
        self.cost_lst = cost_lst
        
        assert len(self.probs_matrix)==len(self.cost_lst)
        assert len(self.convertion_matrix)==len(self.convertion_cost_lst)
        assert self.probs_matrix.shape[1]==self.convertion_matrix.shape[1]
        
        self.equav_cost_lst = np.hstack([cost_lst, convertion_cost_lst])
        self.equav_matrix = np.vstack([probs_matrix, convertion_matrix])
        self.equav_matrix_outc = np.vstack([probs_matrix, convertion_outc_matrix])
        
        
    def update(self, 
               filter_freq=20,
               filter_stages=[],
               url_stats='result/matrix?show_stage_details=true&show_item_details=true',
               url_rules='formula', 
               path_stats='data/matrix.json', 
               path_rules='data/formula.json'):
        """
        To update parameters when probabilities change or new items added.
        Args:
            url_stats: string. url to the dropping rate stats data.
            url_rules: string. url to the composing rules data.
            path_stats: string. local path to the dropping rate stats data.
            path_rules: string. local path to the composing rules data.
        """
        print('Requesting data from web resources (i.e., penguin-stats.io and ak.graueneko.xyz)...', end=' ')
        material_probs, convertion_rules = request_data(penguin_url+url_stats, penguin_url+url_rules, path_stats, path_rules)
        print('done.')

        if filter_freq:
            filtered_probs = []
            for dct in material_probs['matrix']:
                if dct['times']>=filter_freq and dct['stage']['code'] not in filter_stages:
                    filtered_probs.append(dct)
            material_probs['matrix'] = filtered_probs

        self._set_lp_parameters(*self._pre_processing(material_probs, convertion_rules))


    def _get_plan_no_prioties(self, demand_lst, outcome=False):
        """
        To solve linear programming problem without prioties.
        Args:
            demand_lst: list of materials demand. Should include all items (zero if not required).
        Returns:
            strategy: list of required clear times for each stage.
            fun: estimated total cost.
        """
        status_dct = {0: 'Optimization terminated successfully, ',
                      1: 'Iteration limit reached, ',
                      2: 'Problem appears to be infeasible, ',
                      3: 'Problem appears to be unbounded, '}
        
        A_ub = self.equav_matrix_outc if outcome else self.equav_matrix

        solution = linprog(c=np.array(self.equav_cost_lst),
                                          A_ub=-A_ub.T,
                                          b_ub=-np.array(demand_lst),
                                          method='interior-point')

        dual_solution = linprog(c=-np.array(demand_lst),
                                          A_ub=A_ub,
                                          b_ub=np.array(self.equav_cost_lst),
                                          method='interior-point')

        x, fun, status = solution.x, solution.fun, solution.status
        
        n_looting = x[:len(self.cost_lst)]
        n_convertion = x[len(self.cost_lst):]
        strategy = (n_looting, n_convertion)
        
        return strategy, fun, status_dct[status], dual_solution.x


    def get_plan(self, requirement_dct, deposited_dct={}, print_output=True, prioty_dct=None, outcome=False):
        """
        User API. Computing the material plan given requirements and owned items.
        Args:
                requirement_dct: dictionary. Contain only required items with their numbers.
                deposit_dct: dictionary. Contain only owned items with their numbers.
        """
        demand_lst = np.zeros(len(self.item_array))
        for k, v in requirement_dct.items():
            demand_lst[self.item_dct_rv[k]] = v
        for k, v in deposited_dct.items():
            demand_lst[self.item_dct_rv[k]] -= v
        
        stt = time.time()
        (n_looting, n_convertion), cost, status, dual_solution = self._get_plan_no_prioties(demand_lst, outcome)

        if print_output:
            print(status+('Computed in %.4f seconds,' %(time.time()-stt)))

        if status != 'Optimization terminated successfully, ':
            raise ValueError(status)

        stages = []
        for i,t in enumerate(n_looting):
            if t >= 0.1:
                target_items = np.where(self.probs_matrix[i]>=0.05)[0]
                items = {self.item_array[idx]: float2str(self.probs_matrix[i, idx]*t)
                for idx in target_items if len(self.item_id_array[idx])==5}
                stage = {
                    "stage": self.stage_array[i],
                    "count": float2str(t),
                    "items": items
                }
                stages.append(stage)

        syntheses = []
        for i,t in enumerate(n_convertion):
            if t >= 0.1:
                target_item = self.item_array[np.argmax(self.convertion_matrix[i])]
                materials = { k: str(v*int(t+0.9)) for k,v in self.convertions_dct[target_item].items() }
                synthesis = {
                    "target": target_item,
                    "count": str(int(t+0.9)),
                    "materials": materials
                }
                syntheses.append(synthesis)
            elif t >= 0.01:
                target_item = self.item_array[np.argmax(self.convertion_matrix[i])]
                materials = { k: '%.1f'%(v*t) for k,v in self.convertions_dct[target_item].items() }
                synthesis = {
                    "target": target_item,
                    "count": '%.1f'%t,
                    "materials": materials
                }
                syntheses.append(synthesis)

        res = {
            "cost": int(cost),
            "stages": stages,
            "syntheses": syntheses
        }

        if print_output:
            print('Estimated total cost', res['cost'])
            print('Loot at following stages:')
            for stage in stages:
                display_lst = [k + '(%s) '%stage['items'][k] for k in stage['items']]
                print('Stage ' + stage['stage'] + '(%s times) ===> '%stage['count']
                + ', '.join(display_lst))
            print('Synthesize following items:')
            for synthesis in syntheses:
                display_lst = [k + '(%s) '%synthesis['materials'][k] for k in synthesis['materials']]
                print(synthesis['target'] + '(%s) <=== '%synthesis['count']
                + ', '.join(display_lst))
            print('Items Values:')
            for i,item in enumerate(self.item_array):
                print(item+': %.2f'%dual_solution[i])

        return res


def Cartesian_sum(arr1, arr2):
    arr_r = []
    for arr in arr1:
        arr_r.append(arr+arr2)
    arr_r = np.vstack(arr_r)
    return arr_r

def float2str(x, offset=0.5):

    if x < 1.0:
        out = '%.1f'%x
    else:
        out = '%d'%(int(x+offset))
    return out

def request_data(url_stats, url_rules, save_path_stats, save_path_rules):
    """
    To request probability and convertion rules from web resources and store at local.
    Args:
        url_stats: string. url to the dropping rate stats data.
        url_rules: string. url to the composing rules data.
        save_path_stats: string. local path for storing the stats data.
        save_path_rules: string. local path for storing the composing rules data.
    Returns:
        material_probs: dictionary. Content of the stats json file.
        convertion_rules: dictionary. Content of the rules json file.
    """
    try:
        os.mkdir(os.path.dirname(save_path_stats))
    except:
        pass
    try:
        os.mkdir(os.path.dirname(save_path_rules))
    except:
        pass
    
    with urllib.request.urlopen(url_stats) as url:
        material_probs = json.loads(url.read().decode())
        with open(save_path_stats, 'w') as outfile:
            json.dump(material_probs, outfile)

    with urllib.request.urlopen(url_rules) as url:
        convertion_rules = json.loads(url.read().decode())
        with open(save_path_rules, 'w') as outfile:
            json.dump(convertion_rules, outfile)

    return material_probs, convertion_rules

def load_data(path_stats, path_rules):
    """
    To load stats and rules data from local directories.
    Args:
        path_stats: string. local path to the stats data.
        path_rules: string. local path to the composing rules data.
    Returns:
        material_probs: dictionary. Content of the stats json file.
        convertion_rules: dictionary. Content of the rules json file.
    """
    with open(path_stats) as json_file:  
        material_probs  = json.load(json_file)
    with open(path_rules) as json_file:  
        convertion_rules  = json.load(json_file)

    return material_probs, convertion_rules
