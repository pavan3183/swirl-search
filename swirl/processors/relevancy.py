'''
@author:     Sid Probstein
@contact:    sid@swirl.today
'''

from math import sqrt
from statistics import mean, median

from django.conf import settings

# to do: detect language and load all stopwords? P1
from swirl.nltk import stopwords, sent_tokenize
from swirl.processors.utils import clean_string, bigrams, stem_string, match_all, match_any, highlight_list, remove_tags
from swirl.spacy import nlp
from swirl.processors.processor import PostResultProcessor

#############################################    
#############################################    

class CosineRelevancyProcessor(PostResultProcessor):

    # This processor loads a set of saved results, scores them, and updates the results
    type = 'CosineRelevancyPostResultProcessor'

    ############################################

    def process(self):

        RELEVANCY_CONFIG = settings.SWIRL_RELEVANCY_CONFIG
        
        # prep query string
        query = clean_string(self.search.query_string_processed).strip()
        query_list = query.strip().split()
        # remove AND, OR
        if 'AND' in query_list:
            query_list.remove('AND')
        if 'OR' in query_list:
            query_list.remove('OR')
        # not list
        not_list = []
        not_parsed_query = []
        if 'NOT' in query_list:
            not_parsed_query = query_list[:query_list.index('NOT')]
            not_list = query_list[query_list.index('NOT')+1:]
        else:
            for q in query_list:
                if q.startswith('-'):
                    not_list.append(q[1:])
                else:
                    not_parsed_query.append(q)
                # end if
            # end for
        # end if
        if not_parsed_query:
            query = ' '.join(not_parsed_query).strip()
            query_list = query.split()
        # end if
        query_nlp = nlp(query)

        # check for zero vector
        empty_query_vector = False
        if query_nlp.vector.all() == 0:
            empty_query_vector = True

        # check for stopword query
        query_without_stopwords = []
        for extract in query_list:
            if not extract in stopwords:
                query_without_stopwords.append(extract)
        if len(query_without_stopwords) == 0:
            self.error(f"query_string_processed is all stopwords!")
            # to do: handle more gracefully
            return self.results

        # stem the query - fix for https://github.com/sidprobstein/swirl-search/issues/34
        query_stemmed_list = stem_string(clean_string(query)).strip().split()
        query_stemmed_list_len = len(query_stemmed_list)

        # check for non query?
        if query_stemmed_list_len == 0:
            self.warning("Query stemmed list is empty!")
            return self.results

        updated = 0
        dict_lens = {}

        # prepare query targets
        query_stemmed_target_list = []
        query_target_list = []
        # 1 gram
        if query_stemmed_list_len == 1:
            query_stemmed_target_list.append(query_stemmed_list)
            query_target_list.append(query_list)
        # 2 gram
        if query_stemmed_list_len == 2:
            query_stemmed_target_list.append(query_stemmed_list)
            query_target_list.append(query_list)
            query_stemmed_target_list.append([query_stemmed_list[0]])
            query_target_list.append([query_list[0]])
            query_stemmed_target_list.append([query_stemmed_list[1]])
            query_target_list.append([query_list[1]])
        # more grams
        if query_stemmed_list_len >= 3:
            query_stemmed_target_list.append(query_stemmed_list)
            query_target_list.append(query_list)
            for bigram in bigrams(query_stemmed_list):
                query_stemmed_target_list.append(bigram)
            for bigram in bigrams(query_list):
                query_target_list.append(bigram)
            for gram in query_stemmed_list:
                # ignore stopword 1-grams
                if gram in stopwords:
                    continue
                query_stemmed_target_list.append([gram])
            for gram in query_list:
                # ignore stopword 1-grams
                if gram in stopwords:
                    continue
                query_target_list.append([gram])
        if len(query_stemmed_target_list) != len(query_target_list):
            self.error("len(query_stemmed_target_list) != len(query_target_list), highlighting errors may occur")

        ############################################
        # PASS 1
        for results in self.results:
            ############################################
            # result set
            highlighted_json_results = []
            if not results.json_results:
                continue
            for result in results.json_results:
                ############################################
                # result item
                dict_score = {}
                dict_score['stems'] = ' '.join(query_stemmed_list)
                dict_len = {}
                notted = ""
                for field in RELEVANCY_CONFIG:
                    if field in result:
                        if type(result[field]) == list:
                            # to do: handle this better
                            result[field] = result[field][0]
                        # result_field is shorthand for item[field]
                        result_field = clean_string(result[field]).strip()
                        # check for zero-length result
                        if result_field:
                            if len(result_field) == 0:
                                continue
                        # prepare result field                        
                        result_field_nlp = nlp(result_field)
                        result_field_list = result_field.strip().split()
                        # fix for https://github.com/sidprobstein/swirl-search/issues/34
                        result_field_stemmed = stem_string(result_field)
                        result_field_stemmed_list = result_field_stemmed.strip().split()
                        if len(result_field_list) != len(result_field_stemmed_list):
                            self.error("len(result_field_list) != len(result_field_stemmed_list), highlighting errors may occur")
                        # NOT test
                        for t in not_list:
                            if t.lower() in (result_field.lower() for result_field in result_field_list):
                                notted = {field: t}
                                break
                        # field length
                        if field in dict_len:
                            self.warning("duplicate field?")
                        else:
                            dict_len[field] = len(result_field_list)
                        if field in dict_lens:
                            dict_lens[field].append(len(result_field_list))
                        else:
                            dict_lens[field] = []
                            dict_lens[field].append(len(result_field_list))
                        # initialize
                        dict_score[field] = {}
                        extracted_highlights = []
                        match_stems = []
                        ###########################################
                        # query vs result_field
                        if match_any(query_stemmed_list, result_field_stemmed_list):  
                            qvr = 0.0                          
                            label = '_*'
                            if empty_query_vector or result_field_nlp.vector.all() == 0:
                                if len(result_field_list) == 0:
                                    qvr = 0.0
                                else:
                                    qvr = 0.3 + 1/3
                                # end if
                            else:
                                if len(sent_tokenize(result_field)) > 1:
                                    # by sentence, take highest
                                    max_similarity = 0.0
                                    for sent in sent_tokenize(result_field):
                                        result_sent_nlp = nlp(sent)
                                        qvs = query_nlp.similarity(result_sent_nlp)
                                        if qvs > max_similarity:
                                            max_similarity = qvs
                                    # end for
                                    qvr = max_similarity
                                    label = '_s*'
                                else:
                                    qvr = query_nlp.similarity(result_field_nlp)
                            # end if
                            if qvr >= float(settings.SWIRL_MIN_SIMILARITY):
                                dict_score[field]['_'.join(query_list)+label] = qvr
                        ############################################
                        # score each query target
                        for stemmed_query_target, query_target in zip(query_stemmed_target_list, query_target_list):
                            query_slice_stemmed_list = stemmed_query_target
                            query_slice_stemmed_len = len(query_slice_stemmed_list)
                            if '_'.join(query_target) in dict_score[field]:
                                # already have this query slice in dict_score - should not happen?
                                self.warning(f"{query_target} already in dict_score")
                                continue
                            ####### MATCH
                            # iterate across all matches, match on stem
                            # match_all returns a list of result_field_list indexes that match
                            match_list = match_all(query_slice_stemmed_list, result_field_stemmed_list)
                            # truncate the match list, if longer than configured
                            if len(match_list) > settings.SWIRL_MAX_MATCHES:
                                self.warning(f"truncating matches for: {query_slice_stemmed_list}")
                                match_list = match_list[:settings.SWIRL_MAX_MATCHES-1]
                            qw_list = query_target
                            if match_list:
                                key = ''
                                for match in match_list:
                                    extracted_match_list = result_field_list[match:match+query_slice_stemmed_len]
                                    key = '_'.join(extracted_match_list)+'_'+str(match)
                                    rw_list = result_field_list[match-(2*query_slice_stemmed_len):match+(2*query_slice_stemmed_len)+1]
                                    dict_score[field][key] = 0.0
                                    ######## SIMILARITY vs WINDOW
                                    rw_nlp = nlp(' '.join(rw_list))
                                    if rw_nlp.vector.all() == 0:
                                        dict_score[field][key] = 0.31 + 1/3
                                    qw_nlp = nlp(' '.join(qw_list))
                                    if qw_nlp.vector.all() == 0:
                                        dict_score[field][key] = 0.32 + 1/3
                                    if dict_score[field][key] == 0.0:
                                        qw_nlp_sim = qw_nlp.similarity(rw_nlp)
                                        if qw_nlp_sim:
                                            if qw_nlp_sim >= float(settings.SWIRL_MIN_SIMILARITY):
                                                dict_score[field][key] = qw_nlp_sim
                                    if dict_score[field][key] == 0.0:
                                        del dict_score[field][key]
                                    ######### COLLECT MATCHES FOR HIGHLIGHTING
                                    for extract in extracted_match_list:
                                        if extract in extracted_highlights:
                                            continue
                                        extracted_highlights.append(extract)
                                    if '_'.join(query_slice_stemmed_list) not in match_stems:
                                        match_stems.append('_'.join(query_slice_stemmed_list))
                                # end for
                            # end if match_list
                        # end for
                        if dict_score[field] == {}:
                            del dict_score[field]
                        ############################################
                        # highlight
                        result[field] = result[field].replace('*','')   # remove old
                        # fix for https://github.com/sidprobstein/swirl-search/issues/33
                        result[field] = highlight_list(remove_tags(result[field]), extracted_highlights)
                    # end if
                # end for field in RELEVANCY_CONFIG:
                if notted:
                    result['NOT'] = notted
                else:
                    result['dict_score'] = dict_score
                    result['dict_len'] = dict_len
            # end for result in results.json_results:
        # end for results in self.results:
        ############################################
        # Compute field means
        dict_len_median = {}
        for field in dict_lens:
            dict_len_median[field] = mean(dict_lens[field])
        ############################################
        # PASS 2
        # score results by field, adjusting for field length
        for results in self.results:
            if not results.json_results:
                continue
            for result in results.json_results:
                result['swirl_score'] = 0.0
                # check for not
                if 'NOT' in result:
                    result['swirl_score'] = -1.0 + 1/3
                    result['explain'] = { 'NOT': result['NOT'] }
                    del result['NOT']
                    break
                # retrieve the scores and lens from pass 1
                if 'dict_score' in result:
                    dict_score = result['dict_score']
                    del result['dict_score']
                else:
                    self.warning(f"pass 2: result {results}: {result} has no dict_score")
                if 'dict_len' in result:
                    dict_len = result['dict_len']
                    del result['dict_len']
                else:
                    self.warning(f"pass 2: result {results}: {result} has no dict_len")
                # score the item 
                for f in dict_score:
                    if f in RELEVANCY_CONFIG:
                        weight = RELEVANCY_CONFIG[f]['weight']
                    else:
                        continue
                    for k in dict_score[f]:
                        if k.startswith('_'):
                            continue
                        if not dict_score[f][k]:
                            continue
                        if dict_score[f][k] >= float(settings.SWIRL_MIN_SIMILARITY):
                            len_adjust = float(dict_len_median[f] / dict_len[f])
                            rank_adjust = 1.0 + (1.0 / sqrt(result['searchprovider_rank']))
                            if k.endswith('_*') or k.endswith('_s*'):
                                result['swirl_score'] = result['swirl_score'] + (weight * dict_score[f][k]) * (len(k) * len(k))
                            else:
                                result['swirl_score'] = result['swirl_score'] + (weight * dict_score[f][k]) * (len(k) * len(k)) * len_adjust * rank_adjust
                        # end if
                    # end for
                # end for
                ####### explain
                result['explain'] = dict_score                
                updated = updated + 1
                # save highlighted version
                highlighted_json_results.append(result)
            # end for
            results.save()
        # end for
        ############################################

        self.results_updated = int(updated)
        
        return self.results_updated                