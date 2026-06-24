class Solution {
public:
    vector<vector<int>> generateMatrix(int n) {
  vector<vector<int>> result(n, vector<int>(n, 0));
  int num=1;
  int left=0;
  int right=n-1;
  int top=0;
  int bottom=n-1;
  while(left<=right&&top<=bottom){

  for(int j=left;j<=right;j++){
 result[top][j]=num++;
  }
  top++;


 for(int i=top;i<=bottom;i++){
    result[i][right]=num++;
} 
right--;

if (top <= bottom) {
    for(int j=right;j>=left;j--){
    result[bottom][j]=num++;
}
bottom--;
}
if (left <= right) {
for(int i=bottom;i>=top;i--){
    result[i][left]=num++;
}
left++;
  }
  }
return result;
  }
};