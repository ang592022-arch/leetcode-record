class Solution {
public:
    vector<int> topKFrequent(vector<int>& nums, int k) {
    unordered_map<int,int>map;
      for(int num:nums){
        map[num]++;
      }
      priority_queue<pair<int,int>,vector<pair<int,int>>,greater<pair<int,int>>>pq;
      for(auto it:map){
        pq.push({it.second,it.first});
      
      if(pq.size()>k){
        pq.pop();
      }}
      vector<int>result;
      while(!pq.empty()){
        result.push_back(pq.top().second);
        pq.pop();
      }
      return result;
    }

};